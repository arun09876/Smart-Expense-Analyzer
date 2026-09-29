import base64

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import analysis, cache_utils
from .forms import SignupForm, UploadForm
from .models import CATEGORIES, Statement, Transaction
from .tasks import process_statement


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def dashboard(request):
    uid = request.user.id
    data = cache_utils.cached(uid, "summary", lambda: analysis.summary(analysis.user_frame(request.user)))
    return render(request, "expenses/dashboard.html", {
        "s": data,
        "statements": request.user.statements.all()[:10],
        "form": UploadForm(),
        "max_mb": settings.MAX_UPLOAD_MB,
    })


@login_required
@require_POST
def upload(request):
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for err in form.errors.get("file", ["Please choose a file under the size limit."]):
            messages.error(request, err)
        return redirect("dashboard")
    f = form.cleaned_data["file"]
    statement = Statement.objects.create(user=request.user, original_name=f.name[:255], file_type=f.ext)
    payload = base64.b64encode(f.read()).decode()
    f.close()
    process_statement.delay(statement.id, payload)
    return redirect("statement_status", pk=statement.pk)


@login_required
def statement_status(request, pk):
    statement = get_object_or_404(Statement, pk=pk, user=request.user)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({
            "status": statement.status,
            "progress": statement.progress,
            "error": statement.error,
            "rows": statement.row_count,
        })
    return render(request, "expenses/status.html", {"statement": statement})


@login_required
@require_POST
def statement_delete(request, pk):
    statement = get_object_or_404(Statement, pk=pk, user=request.user)
    statement.delete()
    cache_utils.bump(request.user.id)
    messages.info(request, "Statement and its transactions deleted.")
    return redirect("dashboard")


@login_required
def transactions(request):
    qs = Transaction.objects.filter(statement__user=request.user, statement__status="done")
    category, month, recurring = request.GET.get("category"), request.GET.get("month"), request.GET.get("recurring")
    if category:
        qs = qs.filter(category=category)
    if month and len(month.split("-")) == 2 and month.replace("-", "").isdigit():
        year, mon = month.split("-")
        qs = qs.filter(date__year=year, date__month=mon)
    if recurring:
        qs = qs.filter(is_recurring=True)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    months = qs.model.objects.filter(statement__user=request.user).dates("date", "month", order="DESC")
    return render(request, "expenses/transactions.html", {
        "page": page, "categories": CATEGORIES, "months": months,
        "active": {"category": category, "month": month, "recurring": recurring},
    })


@login_required
@require_POST
def set_category(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, statement__user=request.user)
    category = request.POST.get("category")
    if category not in CATEGORIES:
        return JsonResponse({"error": "Unknown category"}, status=400)
    targets = Transaction.objects.filter(pk=txn.pk)
    if request.POST.get("apply_all") == "1":
        targets = Transaction.objects.filter(statement__user=request.user, merchant=txn.merchant)
    updated = targets.update(category=category, category_source=Transaction.Source.USER)
    cache_utils.bump(request.user.id)
    return JsonResponse({"updated": updated})


CHARTS = {
    "monthly": analysis.monthly_chart,
    "categories": analysis.category_chart,
    "merchants": analysis.merchants_chart,
}


@login_required
def chart(request, name):
    build = CHARTS.get(name)
    if build is None:
        return HttpResponse(status=404)
    png = cache_utils.cached(request.user.id, f"chart:{name}", lambda: build(analysis.user_frame(request.user)))
    return HttpResponse(png, content_type="image/png")
