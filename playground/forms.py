from django.forms import ModelForm
from .models import Room
from django import forms
from .models import UserProfile


class RoomForm(ModelForm):
    class Meta:
        model = Room
        fields= '__all__'
        exclude=['host', 'participants']

class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['avatar', 'bio']