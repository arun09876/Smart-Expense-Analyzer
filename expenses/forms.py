from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm


class SignupForm(UserCreationForm):
    pass


class UploadForm(forms.Form):
    file = forms.FileField(help_text="CSV or PDF bank statement")

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ("csv", "pdf"):
            raise forms.ValidationError("Please upload a .csv or .pdf statement.")
        if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
        head = f.read(5)
        f.seek(0)
        if ext == "pdf" and head != b"%PDF-":
            raise forms.ValidationError("This doesn't look like a valid PDF.")
        f.ext = ext
        return f
