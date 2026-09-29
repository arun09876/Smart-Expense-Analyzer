import base64
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from . import cache_utils
from .analysis import detect_recurring
from .categorize import llm_categories, rule_category
from .models import Statement, Transaction
from .parsers import StatementParseError, parse_statement

log = logging.getLogger(__name__)


def _update(statement, **fields):
    for k, v in fields.items():
        setattr(statement, k, v)
    statement.save(update_fields=list(fields))


@shared_task
def process_statement(statement_id: int, payload_b64: str) -> str:
    """Parse → categorise (rules, then LLM) → detect recurring → save.

    The file only exists in this function's memory; nothing is written to disk.
    """
    statement = Statement.objects.get(pk=statement_id)
    data = base64.b64decode(payload_b64)
    del payload_b64
    try:
        _update(statement, status=Statement.Status.PROCESSING, progress="Reading your statement…")
        df = parse_statement(data, statement.file_type)

        _update(statement, progress=f"Categorising {len(df)} transactions…")
        df["category"] = [rule_category(d, m, dr) for d, m, dr in zip(df["description"], df["merchant"], df["direction"])]
        df["source"] = df["category"].map(lambda c: Transaction.Source.RULE if c else Transaction.Source.NONE)

        unknown = df[df["category"].isna()].drop_duplicates("merchant")[["merchant", "description"]]
        llm_count = 0
        if not unknown.empty:
            _update(statement, progress=f"Asking AI about {len(unknown)} unfamiliar merchants…")
            mapping = llm_categories(unknown.to_dict("records"))
            mask = df["category"].isna() & df["merchant"].isin(mapping)
            df.loc[mask, "category"] = df.loc[mask, "merchant"].map(mapping)
            df.loc[mask, "source"] = Transaction.Source.LLM
            llm_count = int(mask.sum())
        df["category"] = df["category"].fillna("Other")

        _update(statement, progress="Finding recurring payments…")
        recurring = set(detect_recurring(df)["merchant"])

        with transaction.atomic():
            Transaction.objects.bulk_create(
                Transaction(
                    statement=statement, date=row.date.date(), description=row.description[:500],
                    merchant=row.merchant, amount=round(row.amount, 2), direction=row.direction,
                    category=row.category, category_source=row.source, is_recurring=row.merchant in recurring,
                )
                for row in df.itertuples()
            )
            _update(
                statement, status=Statement.Status.DONE, progress="", row_count=len(df), llm_categorized=llm_count,
                period_start=df["date"].min().date(), period_end=df["date"].max().date(), processed_at=timezone.now(),
            )
        cache_utils.bump(statement.user_id)
        return "done"
    except StatementParseError as e:
        _update(statement, status=Statement.Status.FAILED, error=str(e), progress="")
        return "failed"
    except Exception:
        log.exception("Statement %s failed", statement_id)
        _update(statement, status=Statement.Status.FAILED, progress="",
                error="Something went wrong while processing this file. Please try the CSV export.")
        return "failed"
    finally:
        del data  # the uploaded file is never persisted
