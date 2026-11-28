"""Fixed, fictional vocabulary for background data.

Names are combined at random from common given names and surnames; they do not describe
real people. Product names are unbranded descriptions. Carriers are invented. Emails always
use the reserved ``example.test`` domain.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

FIRST_NAMES: Final = (
    "Avery", "Blake", "Cameron", "Dakota", "Elliot", "Finley", "Harper", "Jordan",
    "Kendall", "Logan", "Morgan", "Noel", "Parker", "Quinn", "Reese", "Riley",
    "Rowan", "Sage", "Skyler", "Taylor", "Alex", "Casey", "Drew", "Emerson",
    "Hayden", "Jamie", "Kai", "Lane", "Marlowe", "Oakley", "Peyton", "Remy",
)  # fmt: skip

LAST_NAMES: Final = (
    "Abbott", "Bramwell", "Calloway", "Delacroix", "Ellsworth", "Fairbanks", "Galloway",
    "Hartwell", "Ingram", "Jansen", "Kingsley", "Lockhart", "Merriweather", "Northcott",
    "Okafor", "Pemberton", "Quintero", "Ravenscroft", "Sandoval", "Thornbury", "Underhill",
    "Vasquez", "Whitlock", "Yarborough", "Zeller", "Ashdown", "Brightwater", "Coldwell",
)  # fmt: skip

ADJECTIVES: Final = (
    "Compact", "Classic", "Deluxe", "Everyday", "Foldable", "Lightweight", "Modern",
    "Portable", "Rustic", "Sleek", "Sturdy", "Travel",
)  # fmt: skip

CategorySpec = tuple[tuple[str, ...], int, int]
"""(nouns, minimum list price in whole dollars, maximum list price in whole dollars)."""

RETURNABLE_CATEGORIES: Final[Mapping[str, CategorySpec]] = MappingProxyType(
    {
        "kitchen": (("Ceramic Mug Set", "Chef Knife", "Cutting Board", "Saucepan"), 9, 149),
        "audio": (("Bluetooth Speaker", "Earbuds", "Soundbar"), 29, 399),
        "home_office": (("Desk Organizer", "Monitor Stand", "Task Chair"), 14, 349),
        "outdoor": (("Camping Lantern", "Hiking Daypack", "Picnic Blanket"), 12, 189),
        "home_textiles": (("Bath Towel Set", "Duvet Cover", "Throw Pillow"), 15, 179),
    }
)

NON_RETURNABLE_CATEGORIES: Final[Mapping[str, CategorySpec]] = MappingProxyType(
    {
        "clearance": (("Display Model Lamp", "Final Sale Vase", "Open Box Kettle"), 9, 99),
        "personal_care": (("Electric Toothbrush Head", "Hair Trimmer"), 6, 79),
        "perishables": (("Gourmet Coffee Beans", "Loose Leaf Tea Tin"), 4, 39),
    }
)

CARRIERS: Final = (
    "Parcelway Synthetic Freight",
    "Northbound Demo Courier",
    "Lakeside Test Logistics",
)
