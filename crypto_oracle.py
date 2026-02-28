import requests

class CryptoOracle:
    def __init__(self, symbol="BTCUSDT"):
        self.url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"

    def get_binance_price(self):
        """Gets the ultra-fast spot price from Binance."""
        try:
            return float(requests.get(self.url).json()['price'])
        except: return None

    def check_strike_opportunity(self, target_price, side="above", current_poly_price=0.5):
        """
        Decision Logic:
        If we need price 'above' 95000, and Binance says 95100,
        but Polymarket is still selling YES for $0.60... it's a GO.
        """
        real_price = self.get_binance_price()
        if not real_price: return False

        if side == "above" and real_price > (target_price + 10): # +10 for safety buffer
            if current_poly_price < 0.85:
                return True
        return False
