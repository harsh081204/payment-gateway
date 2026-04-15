import random
from app.models import ChargeStatus

class PaymentProcessorService:
    @staticmethod
    def process_payment(amount: int, currency: str) -> ChargeStatus:
        """
        Mock payment processor service.
        Returns succeeded 90% of the time and failed 10% of the time.
        """
        # Simulate network latency/processing time
        # (In a real scenario, we might use asyncio.sleep)
        
        if random.random() < 0.9:
            return ChargeStatus.succeeded
        return ChargeStatus.failed

payment_processor = PaymentProcessorService()
