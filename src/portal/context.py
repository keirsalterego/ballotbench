"""What every page's header needs to know about the signed-in person."""
from .models import Membership


def nav(request):
    user = request.user
    return {"nav_judge": user.is_authenticated and Membership.objects.filter(
        user=user, role=Membership.Role.JUDGE).exists()}
