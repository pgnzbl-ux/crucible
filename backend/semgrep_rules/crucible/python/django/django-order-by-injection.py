def bad_order_by(request, Model=None):
    col = request.GET.get("o")
    # ruleid: django-order-by-injection
    Model.objects.order_by(col)


def bad_order_by_post(request, Model=None):
    col = request.POST["sort"]
    # ruleid: django-order-by-injection
    Model.objects.filter(active=True).order_by(col)


def bad_extra_order_by(request, Model=None):
    col = request.GET.get("o")
    # ruleid: django-order-by-injection
    Model.objects.extra(order_by=[col])


def bad_extra_order_by_kw(request, Model=None):
    col = request.POST.get("o")
    # ruleid: django-order-by-injection
    Model.objects.extra(order_by=col)


def safe_literal(request, Model=None):
    # ok: django-order-by-injection
    Model.objects.order_by("id")


def safe_whitelist(request, Model=None):
    allowed = {"id": "id", "name": "name"}
    key = request.GET.get("o")
    col = allowed.get(key, "id")
    # ok: django-order-by-injection
    Model.objects.order_by(col)
