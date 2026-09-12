from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    lead_days = forms.IntegerField(
        min_value=1, max_value=60, widget=forms.NumberInput(attrs={"inputmode": "numeric"})
    )

    class Meta:
        model = Supplier
        fields = ["name", "lead_days", "phone", "email"]

    def clean_name(self):
        # The model's uniqueness constraint is on Lower(name), an expression
        # Django's automatic validate_unique() can't introspect - so a
        # duplicate would otherwise reach the database as a raw IntegrityError
        # instead of a friendly form error.
        name = self.cleaned_data["name"]
        clash = Supplier.objects.filter(organisation=self.instance.organisation, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have a supplier with that name.")
        return name
