"""Canonical schema, column aliases, category rules, and dayparts.

Every input source (POS export, Excel, SQL, PDF table) is mapped onto these
canonical fields before anything else runs. Add aliases here when a new
POS system names its columns differently, or pass a JSON mapping file.
"""

from __future__ import annotations

# Canonical field -> accepted source column names (matched case-insensitively,
# after stripping spaces/underscores).
COLUMN_ALIASES: dict[str, list[str]] = {
    "transaction_id": ["transaction_id", "transactionid", "receipt", "receipt_id", "trans_id", "ticket", "ticket_number"],
    "timestamp": ["timestamp", "datetime", "date_time", "sale_time", "transaction_time", "date", "sale_date"],
    "customer_id": ["customer_id", "customerid", "loyalty_id", "member_id", "card_id", "rewards_id"],
    "email": ["email", "email_address", "e_mail"],
    "phone": ["phone", "phone_number", "mobile", "cell"],
    "product": ["product", "item", "item_name", "description", "sku_name", "product_name"],
    "category": ["category", "department", "dept", "product_category", "class"],
    "quantity": ["quantity", "qty", "units"],
    "amount": ["amount", "line_total", "total", "sales", "revenue", "ext_price", "extended_price", "net_sales"],
}

# Keyword rules for normalizing raw category text into business categories.
# First match wins. "restricted" items (age-gated) are tracked but are never
# used as a targeting or messaging hook.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("restricted", ["tobacco", "cigarette", "vape", "nicotine", "lottery", "lotto", "beer", "wine", "alcohol", "liquor"]),
    ("fuel", ["fuel", "gas", "gasoline", "diesel", "pump"]),
    ("food_service", ["food", "hot", "pizza", "sandwich", "breakfast", "grill", "taquito", "entree", "bakery"]),
    ("beverage", ["beverage", "drink", "soda", "coffee", "energy", "water", "juice", "tea"]),
    ("snack", ["snack", "candy", "chip", "chocolate", "gum", "cookie"]),
]
DEFAULT_CATEGORY = "other"

# Hour-of-day -> daypart label. Start hour inclusive, end exclusive.
DAYPARTS: list[tuple[str, int, int]] = [
    ("late_night", 22, 24),
    ("late_night", 0, 5),
    ("commute_am", 5, 9),
    ("midday", 9, 14),
    ("afternoon", 14, 17),
    ("commute_pm", 17, 22),
]

# Gross margin rate by category, as a share of sales. PLACEHOLDERS, not your
# store's numbers. Replace them with actual category margins, or pass
# --margins margins.json. Value ranking and "share of gross profit" depend on these.
MARGIN_RATES: dict[str, float] = {
    "fuel": 0.09,
    "food_service": 0.55,
    "beverage": 0.40,
    "snack": 0.35,
    "other": 0.30,
    "restricted": 0.15,
}
DEFAULT_MARGIN = 0.30

# Thresholds for the rule-based segmentation (tune with real data).
RULES = {
    "lapse_recency_days": 30,       # no visit in this many days = lapsing
    "lapse_min_visits": 3,          # visits required in the regular-period below to count as lapsing
    "lapse_regular_window": (120, 30),  # regular-period = days ago [120, 30]; must have been a regular then
    "pattern_min_visits": 3,        # visits needed before a time/category pattern counts
    "loyal_min_visits": 12,         # visits in the window for "Loyal Core"
    "loyal_max_recency_days": 30,
    "late_night_share": 0.35,
    "fuel_share": 0.40,
    "commute_share": 0.50,
    "food_share": 0.40,
    "lunch_share": 0.40,
    "min_customers_for_persona": 1,
}

# Playbook per segment: the marketing decisions a store manager can act on.
# "tags" are matched against trend-feed relevance tags.
PLAYBOOK: dict[str, dict] = {
    "Loyal Core": {
        "summary": "High-frequency, recent visitors who already trust the store.",
        "goal": "Protect frequency and grow basket size.",
        "offer_angle": "Reward loyalty: bonus points on their most-bought category, early access to promotions.",
        "channel": "App push / loyalty SMS",
        "timing": "Weekly, aligned to their peak daypart",
        "avoid": "Deep discounts that train them to wait for offers.",
        "tags": ["loyalty", "routine", "rewards"],
    },
    "Lapsing Regulars": {
        "summary": "Were visiting regularly but have gone quiet.",
        "goal": "Win back within 30 to 60 days before they become one-time visitors.",
        "offer_angle": "Win-back offer on the product they bought most, time-limited.",
        "channel": "SMS or email with a reason to return",
        "timing": "Trigger on day 30 without a visit",
        "avoid": "Generic 'we miss you' messages with no offer.",
        "tags": ["comeback", "value", "deal"],
    },
    "Late-Night Regulars": {
        "summary": "Shop after 10 pm; likely shift workers, students, or night drivers.",
        "goal": "Own the late-night occasion.",
        "offer_angle": "Late-night bundles (drink + snack), hot-food timing, safety and convenience messaging.",
        "channel": "In-store signage, app geofence near closing hours",
        "timing": "Evenings and weekends, 9 pm to close",
        "avoid": "Daytime-oriented promotions that hit when they are asleep.",
        "tags": ["late night", "shift work", "nightlife", "event"],
    },
    "Fuel-and-Go Commuters": {
        "summary": "Fill up and grab something on the way to or from work.",
        "goal": "Turn fuel visits into in-store purchases.",
        "offer_angle": "Fuel-plus-item bundle (coffee or drink with a fill-up); commute-time offers.",
        "channel": "Pump-side screens, app offers at fueling",
        "timing": "Weekday mornings and evenings",
        "avoid": "Long-form messaging; they decide in seconds.",
        "tags": ["commute", "gas prices", "traffic", "weather"],
    },
    "Lunch Grab-and-Go": {
        "summary": "Buy food-service items at midday.",
        "goal": "Make the store the default lunch option on workdays.",
        "offer_angle": "Lunch combo or meal deal; hot-food availability and speed messaging.",
        "channel": "In-store menu boards, nearby-office digital ads",
        "timing": "Weekdays 10 am to 1 pm",
        "avoid": "Weekend-only promos for a weekday audience.",
        "tags": ["lunch", "office", "work week", "food trend"],
    },
    "Occasional Visitors": {
        "summary": "Visit a few times, with no dominant pattern yet.",
        "goal": "Convert to a second and third visit.",
        "offer_angle": "Sign-up incentive for the loyalty program; first-visit-to-second-visit reward.",
        "channel": "Receipt QR code, in-store sign-up",
        "timing": "Within 7 days of first visit",
        "avoid": "Heavy discounting to people who would have paid full price.",
        "tags": ["new", "sign up", "community", "local event"],
    },
}
