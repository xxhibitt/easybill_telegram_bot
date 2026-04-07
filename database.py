import aiosqlite
import datetime
import logging
from typing import Optional, List, Dict, Any

DB_NAME = 'easybill.db'
logger = logging.getLogger(__name__)

async def init_db() -> None:
    """
    Initialize the SQLite database asynchronously.
    Creates necessary tables (users, offline_profiles, transactions) if they do not exist.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS offline_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_id INTEGER,
                    name TEXT,
                    FOREIGN KEY (creator_id) REFERENCES users(telegram_id)
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creditor_id INTEGER,
                    debtor_id INTEGER,
                    amount INTEGER,
                    reason TEXT,
                    status TEXT,
                    timestamp TEXT
                )
            ''')
            await db.commit()
            logger.info("Database initialized successfully.")
    except aiosqlite.Error as e:
        logger.error(f"Error initializing database: {e}")
        raise

async def add_user(telegram_id: int, username: str) -> None:
    """
    Add a new user to the users table or update their username if they already exist.
    
    Args:
        telegram_id (int): Built-in Telegram user ID.
        username (str): The Telegram username.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                INSERT INTO users (telegram_id, username)
                VALUES (?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username
            ''', (telegram_id, username))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Error adding/updating user {telegram_id}: {e}")

async def get_user_by_username(username: str) -> Optional[aiosqlite.Row]:
    """Retrieve a user record by their Telegram username."""
    username = username.lstrip('@')
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM users WHERE username = ?', (username,)) as cursor:
                return await cursor.fetchone()
    except aiosqlite.Error as e:
        logger.error(f"Error fetching user {username}: {e}")
        return None

async def add_offline_profile(creator_id: int, name: str) -> Optional[int]:
    """
    Create a new offline profile tied to a real user.
    
    Args:
        creator_id (int): The telegram_id of the user who owns this profile.
        name (str): The display name of the offline profile.
        
    Returns:
        int: The newly created offline profile ID.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('''
                INSERT INTO offline_profiles (creator_id, name)
                VALUES (?, ?)
            ''', (creator_id, name))
            await db.commit()
            return cursor.lastrowid
    except aiosqlite.Error as e:
        logger.error(f"Error creating offline profile '{name}': {e}")
        return None

async def get_offline_profile_by_name(creator_id: int, name: str) -> Optional[aiosqlite.Row]:
    """Retrieve an offline profile by name, specific to the creator."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM offline_profiles WHERE creator_id = ? AND name = ?', (creator_id, name)) as cursor:
                return await cursor.fetchone()
    except aiosqlite.Error as e:
        logger.error(f"Error fetching offline profile '{name}': {e}")
        return None

async def log_transaction(creditor_id: int, debtor_id: int, amount: int, reason: str, status: str) -> Optional[int]:
    """
    Log a formal debt transaction.
    
    Args:
        creditor_id (int): The ID of the person who is owed.
        debtor_id (int): The ID of the person who owes the money.
        amount (int): The monetary amount of the debt.
        reason (str): Label or description of the debt.
        status (str): Either 'pending' or 'confirmed'.
        
    Returns:
        int: The transaction ID.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('''
                INSERT INTO transactions (creditor_id, debtor_id, amount, reason, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (creditor_id, debtor_id, amount, reason, status, datetime.datetime.utcnow().isoformat()))
            await db.commit()
            return cursor.lastrowid
    except aiosqlite.Error as e:
        logger.error(f"Error logging transaction: {e}")
        return None

async def update_transaction_status(tx_id: int, status: str) -> None:
    """Update the status string of a specific transaction."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                UPDATE transactions SET status = ? WHERE id = ?
            ''', (status, tx_id))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Error updating transaction {tx_id}: {e}")

async def get_transaction(tx_id: int) -> Optional[aiosqlite.Row]:
    """Retrieve a single transaction object by ID."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM transactions WHERE id = ?', (tx_id,)) as cursor:
                return await cursor.fetchone()
    except aiosqlite.Error as e:
        logger.error(f"Error fetching transaction {tx_id}: {e}")
        return None

async def get_net_balances(user_id: int) -> List[Dict[str, Any]]:
    """
    Calculates the netted balance between the requested user and all active parties.
    Automatically offsets mutual debts (i.e. if A owes B 1000 and B owes A 500, net is 500).
    
    Args:
        user_id (int): Telegram user ID.
        
    Returns:
        List containing dictionaries of net balances:
        [{'party_id': 123, 'party_name': '@username', 'net': 500, 'is_user': True}]
        Positive net means the party owes the user. Negative means user owes the party.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            balances = {}
            
            # The party acts as debtor (They owe the queried user money). Increments balance positively.
            async with db.execute('''
                SELECT debtor_id, SUM(amount) as total
                FROM transactions
                WHERE creditor_id = ? AND status = 'confirmed'
                GROUP BY debtor_id
            ''', (user_id,)) as cursor:
                async for row in cursor:
                    balances[row['debtor_id']] = balances.get(row['debtor_id'], 0) + row['total']
                    
            # The queried user acts as debtor (User owes the party). Decrements balance.
            async with db.execute('''
                SELECT creditor_id, SUM(amount) as total
                FROM transactions
                WHERE debtor_id = ? AND status = 'confirmed'
                GROUP BY creditor_id
            ''', (user_id,)) as cursor:
                async for row in cursor:
                    balances[row['creditor_id']] = balances.get(row['creditor_id'], 0) - row['total']
                    
            results = []
            for party_id, net_amount in balances.items():
                if net_amount == 0:
                    continue
                    
                # Try to resolve identity from registered Telegram users
                async with db.execute('SELECT username FROM users WHERE telegram_id = ?', (party_id,)) as cursor:
                    user_row = await cursor.fetchone()
                    if user_row:
                        party_name = f"@{user_row['username']}" if user_row['username'] else f"User {party_id}"
                        results.append({'party_id': party_id, 'party_name': party_name, 'net': net_amount, 'is_user': True})
                        continue
                        
                # Try to resolve identity mapped from offline_profiles
                async with db.execute('SELECT name FROM offline_profiles WHERE id = ?', (party_id,)) as cursor:
                    off_row = await cursor.fetchone()
                    if off_row:
                        results.append({'party_id': party_id, 'party_name': off_row['name'], 'net': net_amount, 'is_user': False})
                        continue
                        
                # Party couldn't be properly labeled, fallback
                results.append({'party_id': party_id, 'party_name': f"Unknown ({party_id})", 'net': net_amount, 'is_user': False})
                
            return results
    except aiosqlite.Error as e:
        logger.error(f"Error calculating net balances for user {user_id}: {e}")
        return []

async def get_recent_transactions(user_id: int, limit: int = 10) -> List[aiosqlite.Row]:
    """Retrieve chronologically sorted recent transactions involving the specified user."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM transactions
                WHERE creditor_id = ? OR debtor_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (user_id, user_id, limit)) as cursor:
                return await cursor.fetchall()
    except aiosqlite.Error as e:
        logger.error(f"Error fetching recent transactions for {user_id}: {e}")
        return []

async def delete_transaction(tx_id: int) -> None:
    """Delete a pending transaction from the database."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('DELETE FROM transactions WHERE id = ?', (tx_id,))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Error deleting transaction {tx_id}: {e}")

async def update_transaction_reason(tx_id: int, reason: str) -> None:
    """Update the logical reason for a transaction."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('UPDATE transactions SET reason = ? WHERE id = ?', (reason, tx_id))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Error updating transaction reason {tx_id}: {e}")
