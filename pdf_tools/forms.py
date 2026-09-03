from django import forms


class PdfinoForm(forms.Form):
    """Base form that auto-applies Bootstrap-friendly CSS classes to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get('class', '')
            if isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = 'form-select'
            elif isinstance(widget, forms.Textarea):
                css = 'form-control'
            else:
                css = 'form-control'
            widget.attrs['class'] = (existing + ' ' + css).strip()


class SplitForm(PdfinoForm):
    MODE_CHOICES = [('ranges', 'By page ranges'), ('every_page', 'Every page as its own PDF')]
    mode = forms.ChoiceField(choices=MODE_CHOICES, initial='ranges')
    ranges = forms.CharField(required=False, max_length=500,
                              widget=forms.TextInput(attrs={'placeholder': 'e.g. 1-3, 5, 7-10'}))


class PageSelectionForm(PdfinoForm):
    pages = forms.CharField(max_length=500, widget=forms.TextInput(attrs={'placeholder': 'e.g. 1-3, 5, 7-10'}))


class RotateForm(PdfinoForm):
    SCOPE_CHOICES = [('all', 'All pages'), ('selected', 'Selected pages')]
    degrees = forms.ChoiceField(choices=[(90, '90°'), (180, '180°'), (270, '270°')])
    scope = forms.ChoiceField(choices=SCOPE_CHOICES, initial='all')
    pages = forms.CharField(required=False, max_length=500,
                             widget=forms.TextInput(attrs={'placeholder': 'e.g. 1-3, 5'}))


class CompressForm(PdfinoForm):
    LEVEL_CHOICES = [('low', 'Low (best quality)'), ('medium', 'Medium (recommended)'), ('high', 'High (smallest file)')]
    level = forms.ChoiceField(choices=LEVEL_CHOICES, initial='medium')


class WatermarkForm(PdfinoForm):
    POSITION_CHOICES = [
        ('center', 'Center'), ('top-left', 'Top left'), ('top-right', 'Top right'),
        ('bottom-left', 'Bottom left'), ('bottom-right', 'Bottom right'),
    ]
    text = forms.CharField(max_length=100)
    font_size = forms.IntegerField(min_value=8, max_value=200, initial=40)
    opacity = forms.FloatField(min_value=0.05, max_value=1.0, initial=0.3)
    rotation = forms.IntegerField(min_value=0, max_value=360, initial=45)
    position = forms.ChoiceField(choices=POSITION_CHOICES, initial='center')
    scope = forms.ChoiceField(choices=[('all', 'All pages'), ('selected', 'Selected pages')], initial='all')
    pages = forms.CharField(required=False, max_length=500)


class PageNumbersForm(PdfinoForm):
    POSITION_CHOICES = [
        ('bottom-right', 'Bottom right'), ('bottom-left', 'Bottom left'),
        ('top-right', 'Top right'), ('top-left', 'Top left'),
    ]
    position = forms.ChoiceField(choices=POSITION_CHOICES, initial='bottom-right')
    start_at = forms.IntegerField(min_value=1, initial=1)


class ProtectForm(PdfinoForm):
    password = forms.CharField(widget=forms.PasswordInput, min_length=4, max_length=128)
    confirm_password = forms.CharField(widget=forms.PasswordInput, min_length=4, max_length=128)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password') != cleaned.get('confirm_password'):
            raise forms.ValidationError('Passwords do not match.')
        return cleaned


class UnlockForm(PdfinoForm):
    password = forms.CharField(widget=forms.PasswordInput, max_length=128)


class MetadataForm(PdfinoForm):
    title = forms.CharField(required=False, max_length=255)
    author = forms.CharField(required=False, max_length=255)
    subject = forms.CharField(required=False, max_length=255)
    keywords = forms.CharField(required=False, max_length=255)


class TextToPdfForm(PdfinoForm):
    title = forms.CharField(required=False, max_length=120, initial='Document')
    text = forms.CharField(widget=forms.Textarea(attrs={'rows': 12, 'placeholder': 'Paste or type your text here...'}), max_length=200_000)


class ImageToPdfOptionsForm(PdfinoForm):
    page_size = forms.ChoiceField(choices=[('a4', 'A4'), ('letter', 'Letter'), ('auto', 'Match image size')], initial='a4')
    orientation = forms.ChoiceField(choices=[('portrait', 'Portrait'), ('landscape', 'Landscape')], initial='portrait')
    margin_mm = forms.IntegerField(min_value=0, max_value=50, initial=0)
    fit = forms.ChoiceField(choices=[('contain', 'Fit within page'), ('stretch', 'Stretch to fill')], initial='contain')


class PdfToImageOptionsForm(PdfinoForm):
    SCOPE_CHOICES = [('all', 'All pages'), ('selected', 'Selected pages')]
    scope = forms.ChoiceField(choices=SCOPE_CHOICES, initial='all')
    pages = forms.CharField(required=False, max_length=500)
    quality = forms.ChoiceField(choices=[(150, 'Standard (150 DPI)'), (300, 'High (300 DPI)')], initial=150)
