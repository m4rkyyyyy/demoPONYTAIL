"""Admin registrations for the journal models."""

from django.contrib import admin

from journal.models import Tag, Trade, TradingAccount


@admin.register(TradingAccount)
class TradingAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "currency", "initial_balance", "created_at")
    list_filter = ("currency",)
    search_fields = ("name", "user__username")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "user")
    search_fields = ("name", "user__username")


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        "symbol",
        "user",
        "asset_class",
        "direction",
        "status",
        "entry_date",
        "net_pnl",
    )
    list_filter = ("status", "direction", "asset_class")
    search_fields = ("symbol", "notes", "account__name", "user__username")
    date_hierarchy = "entry_date"
    filter_horizontal = ("tags",)

    @admin.display(description="user")
    def user(self, obj):
        return obj.account.user.username

    @admin.display(description="net P&L")
    def net_pnl(self, obj):
        return obj.net_pnl
