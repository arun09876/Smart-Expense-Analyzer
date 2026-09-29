from django.conf import settings
from django.db import models

CATEGORIES = [
    "Food & Dining", "Groceries", "Shopping", "Transport", "Fuel", "Bills & Utilities",
    "Rent & Housing", "Entertainment", "Health", "Education", "Travel", "EMI & Loans",
    "Investments", "Insurance", "Cash Withdrawal", "Transfers", "Income", "Other",
]


class Statement(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="statements")
    original_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=4)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    progress = models.CharField(max_length=200, blank=True)
    error = models.TextField(blank=True)
    row_count = models.PositiveIntegerField(default=0)
    llm_categorized = models.PositiveIntegerField(default=0)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name


class Transaction(models.Model):
    class Direction(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    class Source(models.TextChoices):
        RULE = "rule", "Rule"
        LLM = "llm", "AI"
        USER = "user", "You"
        NONE = "none", "Unknown"

    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name="transactions")
    date = models.DateField(db_index=True)
    description = models.CharField(max_length=500)
    merchant = models.CharField(max_length=120, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    direction = models.CharField(max_length=6, choices=Direction.choices)
    category = models.CharField(max_length=40, default="Other")
    category_source = models.CharField(max_length=5, choices=Source.choices, default=Source.NONE)
    is_recurring = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.date} {self.merchant} ₹{self.amount}"
