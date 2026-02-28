import hashlib
import hmac
import os
from eth_account import Account

class HydraWalletManager:
    def __init__(self):
        # Load master seed from .env
        self.master_seed = os.getenv("WALLET_SEED", "").strip()
        Account.enable_unaudited_hdwallet_features()
        
        # Hardcoded Owner Credentials
        self.owner_id = "3652288668"
        self.owner_username = "jluxury929"

    def get_user_vault(self, user_id, username=None):
        """
        Returns the Account object for a specific user.
        If the user is the Owner, it returns the Master Wallet.
        """
        # 1. Check for Master Owner bypass
        if str(user_id) == self.owner_id or (username and username.lower() == self.owner_username):
            if " " in self.master_seed:
                return Account.from_mnemonic(self.master_seed)
            return Account.from_key(self.master_seed)

        # 2. Generate unique deterministic key for other users
        # We use HMAC to derive a child key based on the Telegram User ID
        seed_bytes = self.master_seed.encode('utf-8')
        user_bytes = str(user_id).encode('utf-8')
        
        # Derive a 32-byte private key
        derived_key = hmac.new(seed_bytes, user_bytes, hashlib.sha256).hexdigest()
        
        return Account.from_key(derived_key)

    def generate_all_user_addresses(self, user_id_list):
        """Helper to preview multiple addresses (for admin use)"""
        vaults = {}
        for uid in user_id_list:
            acc = self.get_user_vault(uid)
            vaults[uid] = acc.address
        return vaults
