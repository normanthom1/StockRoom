from django import forms

from .models import Item, Supplier


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


class ItemForm(forms.ModelForm):
    starting_count = forms.IntegerField(
        required=False,
        min_value=0,
        help_text="Optional - how many are on the shelf right now.",
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )

    class Meta:
        model = Item
        fields = ["name", "unit", "supplier", "price", "order_size"]
        widgets = {
            "price": forms.NumberInput(attrs={"inputmode": "decimal", "step": "0.01", "min": 0}),
            "order_size": forms.NumberInput(attrs={"inputmode": "numeric", "min": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.for_org(self.instance.organisation).filter(
            is_active=True
        ).order_by("name")
        if self.instance.pk:
            # Starting count only makes sense once, when the item is created.
            del self.fields["starting_count"]
