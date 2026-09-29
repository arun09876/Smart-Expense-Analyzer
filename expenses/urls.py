from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload, name="upload"),
    path("statements/<int:pk>/", views.statement_status, name="statement_status"),
    path("statements/<int:pk>/delete/", views.statement_delete, name="statement_delete"),
    path("transactions/", views.transactions, name="transactions"),
    path("transactions/<int:pk>/category/", views.set_category, name="set_category"),
    path("charts/<str:name>.png", views.chart, name="chart"),
]
