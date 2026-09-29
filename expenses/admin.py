from django.contrib import admin

from .models import Statement, Transaction


@admin.register(Statement)
class StatementAdmin(admin.ModelAdmin):
    list_display = ("original_name", "user", "status", "row_count", "llm_categorized", "created_at")
    list_filter = ("status", "file_type")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "merchant", "amount", "direction", "category", "category_source", "is_recurring")
    list_filter = ("category", "category_source", "direction", "is_recurring")
    search_fields = ("merchant", "description")
