from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


RESTAURANT_ID = "162089e6-09b0-5928-944b-2906df01f10e"
RESTAURANT_SLUG = "restaurang-al-forno"
SOURCE_URLS = (
    "https://www.alforno73.se/pizza",
    "https://www.alforno73.se/pasta",
    "https://www.alforno73.se/sallad",
    "https://www.alforno73.se/kebab",
)


def _rows(value: str) -> list[tuple[str, int, str]]:
    result = []
    for raw_line in value.strip().splitlines():
        name, price, description = (part.strip() for part in raw_line.split("|", 2))
        result.append((name, int(price), description))
    return result


PIZZAS = _rows(
    """
    Margherita|125|Tomatsås, mozzarellaost
    Ruccola|150|Tomatsås, mozzarellaost, ruccola, olivolja och kristallsalt
    Funghi|125|Tomatsås, mozzarellaost och färska champinjoner
    Vesuvio|129|Tomatsås, mozzarellaost och skinka
    Pompei|129|Tomatsås, ost, bacon, lök och ägg
    Hawaii|129|Tomatsås, mozzarellaost, skinka och ananas
    Tropicana|140|Tomatsås, mozzarellaost, skinka, ananas, banan och curry
    Capricciosa|129|Tomatsås, mozzarellaost, skinka och färska champinjoner
    Bolognese|129|Tomatsås, mozzarellaost, köttfärs och lök
    Marinara|135|Tomatsås, mozzarellaost, musslor och räkor
    Gorgonzolapizza|145|Tomatsås, mozzarellaost, skinka, färska tomater och gorgonzolaost
    Quattro Stagioni|149|Tomatsås, mozzarellaost, gröna musslor i skal, italiensk kokt skinka, räkor, färska champinjoner, svarta oliver och basilika
    Bussola|129|Tomatsås, ost, skinka och räkor
    Al Tonno|129|Tomatsås, ost, tonfisk och lök
    Campagnola|135|Tomatsås, ost, salami och lök
    Opera|129|Tomatsås, ost, tonfisk och skinka
    Parma|160|Tomatsås, mozzarellaost, parmaskinka och ruccola
    Buffalo|145|Tomatsås, mozzarellaost, pepperonikorv, champinjoner, lök och paprika
    La Farmacia|145|Tomatsås, mozzarellaost, gorgonzola, fetaost och parmesanost
    Spinatti|160|Tomatsås, mozzarellaost, aubergine, spenat, zucchini, champinjoner, parmesanost och ruccola
    Nikolas|160|Tomatsås, mozzarellaost, salami, bacon, parmaskinka och ruccola
    Sorrento|150|Tomatsås, mozzarellaost, chèvreost, parmesanost, pinjenötter och honung
    Inter|150|Tomatsås, mozzarellaost, fläskfilé, gorgonzola, räkor, champinjoner och tomater
    Di Mare|150|Tomatsås, ost, scampi, räkor, musslor, körsbärstomater och mozzarella
    Bella Rosa|150|Tomatsås, ost, räkor, tonfisk, musslor och scampi
    Ciao Ciao|160|Tomatsås, ost, oxfilé, köttfärs och skinka
    Azteka|150|Tomatsås, mozzarellaost, skinka, tacosås, tacokrydda, jalapeños och vitlökssås
    Mexicana|150|Tomatsås, mozzarellaost, köttfärs, tacosås, tacokrydda, jalapeños, lök och vitlökssås
    Amore|160|Tomatsås, ost, champinjoner och lök
    Banana|129|Tomatsås, ost, skinka, banan och curry
    Yosef|150|Tomatsås, ost, oxfilé, lök, champinjoner, paprika, tomater och bearnaisesås
    Juventus|145|Tomatsås, ost, fetaost, champinjoner, paprika och ruccola
    Kockens Special|160|Tomatsås, ost, mozzarella, skinka, parmaskinka, soltorkade tomater och ruccola
    Supreme|149|Tomatsås, ost, skinka, räkor, champinjoner, lök och paprika, ostgratinerad
    Ubåt|140|Tomatsås, ost, oxfilé, champinjoner, färsk vitlök och bearnaisesås
    Pigalle|135|Tomatsås, ost, räkor, champinjoner och lök
    Kycklingpizza|145|Tomatsås, ost, kyckling, ananas och curry
    Calzone|135|Tomatsås, ost och skinka
    Dubbel Calzone Special|160|Tomatsås, ost, skinka, champinjoner och räkor
    Forno|165|Tomatsås, ost, oxfilé, mozzarella, gorgonzola och bearnaisesås
    Selles Special|150|Tomatsås, ost, köttfärs, lök, champinjoner och fetaost
    Roma|150|Tomatsås, ost, oxfilé, gorgonzola, tomater och bearnaisesås
    Dubbel Calzone|140|Tomatsås, ost och skinka
    Calzone Special|160|Skinka och räkor
    Acapulco|160|Tomatsås, mozzarellaost, biff, lök, champinjoner, vitlök, tacosås, tacokrydda, jalapeños och vitlökssås
    Kebabpizza|150|Tomatsås, mozzarellaost, kebabkött, feferoni, lök, tomater, isbergssallad och vitlökssås
    Viking|160|Kebabkött av nötkött, tomatsås, ost, lök, isbergssallad, tomater, feferoni och vitlökssås
    Kebabpizza Special|165|Tomatsås, ost, kebabkött, isbergssallad, tomater, lök, feferoni, pommes och vitlökssås
    Kycklingkebab Pizza|160|Tomatsås, ost, kycklingkebab, isbergssallad, tomater, lök, feferoni och vitlökssås
    Gyrospizza|160|Tomatsås, ost, gyroskebab, isbergssallad, tomater, lök, feferoni och vitlökssås
    Empoli|135|Tomatsås, ost, grön sparris, champinjoner, oliver, lök och majs
    Tutti Frutti|135|Tomatsås, ost, banan, ananas och curry
    Vegetariana|135|Tomatsås, ost, champinjoner, lök, paprika, oliver och kronärtskocka
    Esras|140|Tomatsås, ost, fetaost, mozzarellaost, soltorkade tomater och oliver
    Vegansk Kebabpizza|165|Tomatsås, veganost, vegansk kebab, isbergssallad, tomater, lök och feferoni
    Messina|140|Tomatsås, veganost, champinjoner, tomater, lök och paprika
    Rosella|140|Tomatsås, veganost, champinjoner, majs, paprika och sparris
    """
)

PAN_PIZZAS = _rows(
    """
    Pan Pizza Beef Special|160|Tomatsås, ost, oxfilé, champinjoner, lök, paprika och bearnaisesås
    Pan Pizza Rio|160|Tomatsås, ost, fläskfilé, gorgonzola, champinjoner och mozzarella
    Pan Pizza Catania|160|Tomatsås, ost, köttfärs, lök, champinjoner och cayennepeppar
    Pan Pizza Palermo|160|Tomatsås, ost, kyckling, ananas, banan och curry
    Pan Pizza Florens|160|Tomatsås, ost, champinjoner, lök, paprika, majs och oliver
    """
)

PASTA = _rows(
    """
    Spaghetti Bolognese|129|Italiensk köttfärs av nötkött med svartpeppar
    Penne Arrabbiata|129|Körsbärstomater, chili, vitlök, persilja och tomatsås
    Pasta Funghi|129|Färska champinjoner, persilja och grädde
    Canneloni Classico|145|Färsk pasta med ricottaost, spenat och grädde
    Prosciutto di Parma|149|Basilikapesto, soltorkade tomater och lufttorkad skinka
    Pasta Romana|179|Spaghetti, scampi, räkor, chili, vitlök, tomatsås och grädde
    Spaghetti Carbonara|135|Bacon, svartpeppar, lök och äggula
    Lasagne Al Forno|135|Hemlagad köttfärssås och bechamelsås
    Pasta Genovese|145|Marinerad kycklingfilé, basilikapesto, soltorkade tomater och grädde
    Pasta Pollo|145|Marinerad kycklingfilé, paprika, grädde och curry
    Delizie Romagniola|145|Färska pastastubbar med zucchini, skinka och grädde
    Spaghetti Bigoli|179|Räkor, scampi, pesto, färsk vitlök, sambal och vitt vin
    Pasta Maestro|169|Strimlad biff, färska champinjoner, grädde, tomatsås, vitlök, chili och ost
    Pasta Mums Mums|179|Oxfilé, paprika, champinjoner, pesto, tryffelolja, grädde och ost
    Oxfilé Pasta|169|Oxfilé, champinjoner och grädde
    Pasta Gorgonzola|169|Skinka, gorgonzola, lök och färsk vitlök
    Frutti di Mare|179|Scampi, blåmusslor, räkor, körsbärstomater, färsk vitlök, chili och ruccola
    """
)

SALADS = _rows(
    """
    Gambretti Sallad|135|Handskalade räkor, ägg, avokado, gurka, tomater, lök, citron, paprika och ruccola
    Prosciutto Sallad|135|Romansallad, lufttorkad skinka, brödkrutonger, tomater, soltorkade tomater, rödlök, paprika, parmesanost och ruccola
    Ceasarsallad|135|Romansallad, marinerad kycklingfilé, tomater, paprika, brödkrutonger, rödlök, caesardressing, parmesanost och ruccola
    Kebabsallad|135|Kebabkött, isbergssallad, gurka, lök, paprika, tomater, feferoni och ruccola
    Grekisksallad|135|Fårost, gurka, paprika, tomater, soltorkade tomater, rödlök och ruccola
    Mozarellasallad|135|Mozzarellaost, gurka, paprika, tomater, basilika, soltorkade tomater, rödlök och ruccola
    Tonfisksallad|135|Tonfisk i olja, ägg, tomater, gurka, rödlök, paprika, citron och ruccola
    Ost-Skinksallad|135|Isbergssallad, tomater, gurka, paprika, oliver, ägg, lök och ruccola
    Gyros Sallad|135|Isbergssallad, tomater, gurka, lök, paprika, feferoni och ruccola
    """
)

KEBAB = _rows(
    """
    Kebab med bröd|105|Kebab, isbergssallad, lök, tomater, feferoni och vitlökssås
    Kebabrulle|130|Isbergssallad, kebab, lök, tomater, feferoni och vitlökssås
    Kebabtallrik|135|Isbergssallad, kebab, lök, tomater, feferoni och vitlökssås
    Kycklingkebabtallrik|145|Isbergssallad, lök, tomater, feferoni och vitlökssås
    Kycklingkebab Rulle|140|Kyckling, isbergssallad, tomater, lök och vitlökssås
    Kycklingkebab med bröd|115|Kyckling, isbergssallad, tomater, lök och vitlökssås
    Mixkebabtallrik|150|Kebabkött av nötkött, kycklingkebab, isbergssallad, lök, tomater, feferoni och vitlökssås
    Vegansk Kebab med bröd|125|Isbergssallad, vegansk kebab, lök, tomater, feferoni och vitlökssås
    Gyrostallrik|135|Gyroskött, isbergssallad, lök, tomater, feferoni och vitlökssås
    Falafeltallrik|140|Isbergssallad, falafel, lök, tomater, feferoni och vitlökssås
    Vegansk Kebabtallrik|150|Isbergssallad, lök, tomater, feferoni och vitlökssås
    Gyrosrulle|140|Isbergssallad, gyros, lök, tomater, feferoni och vitlökssås
    Falafelrulle|140|Isbergssallad, falafel, lök, tomater, feferoni och vitlökssås
    Gyros med bröd|115|Isbergssallad, gyros, lök, tomater, feferoni och vitlökssås
    Falafel med bröd|105|Isbergssallad, falafel, lök, tomater, feferoni och vitlökssås
    Vegansk Kebabrulle|150|Isbergssallad, vegansk kebab, lök, tomater, feferoni och vitlökssås
    """
)

BURGERS = _rows(
    """
    Hamburgare|120|Pommes ingår. Isbergssallad, tomater, lök och hamburgerdressing
    Sambalburgare 200g|135|Pommes ingår. Isbergssallad, tomater, lök och bearnaisesås
    """
)

A_LA_CARTE = _rows(
    """
    Fish and Chips|150|Isbergssallad, lök, tomater och remouladsås
    Husets Plankstek|225|Grillad biff med gratinerat potatismos, bearnaisesås, baconlindad sparris, grillad tomat, grillad paprika, grillade champinjoner och rödvinssås
    Grillbiff|215|Grillbiff, grillade grönsaker, rödvinssås och bearnaisesås
    Fläskfilé Black & White|215|Fläskfilé, grillade grönsaker, rödvinssås och bearnaisesås
    Fläskfilé Oscar|215|Fläskfilé, räkor, grillade grönsaker, rödvinssås och bearnaisesås
    Laxtallrik|190|Lax, isbergssallad, lök, tomater och vitlökssås
    Chicken Bits|140|Isbergssallad, lök, tomater och currydressing
    """
)

SIDE_REQUIRED = {
    "Kebabtallrik",
    "Kycklingkebabtallrik",
    "Mixkebabtallrik",
    "Gyrostallrik",
    "Falafeltallrik",
    "Vegansk Kebabtallrik",
    "Fish and Chips",
    "Grillbiff",
    "Fläskfilé Black & White",
    "Fläskfilé Oscar",
    "Laxtallrik",
    "Chicken Bits",
}


APPROVED_ALIASES: dict[str, list[str]] = {
    "Margherita": ["margarita", "margerita"],
    "Funghi": ["funji", "fungi pizza"],
    "Vesuvio": ["vesovio", "vesuvio pizza"],
    "Pompei": ["pompej", "pompei pizza"],
    "Capricciosa": ["caprichosa", "kapricciosa", "kaprichosa", "capriciosa"],
    "Gorgonzolapizza": ["gorgonzola pizza"],
    "Quattro Stagioni": ["quattro stagione", "quatro stagioni", "kvattro stagioni", "kvattro stagione"],
    "Al Tonno": ["al tono"],
    "Campagnola": ["campanjola", "kampanjola"],
    "La Farmacia": ["farmacia", "la farmasia"],
    "Spinatti": ["spinati"],
    "Sorrento": ["sorento"],
    "Di Mare": ["di mare pizza"],
    "Ciao Ciao": ["tjao tjao", "chao chao", "ciaociao"],
    "Acapulco": ["akapulko", "acapulko"],
    "Kycklingpizza": ["kyckling pizza"],
    "Kebabpizza": ["kebab pizza"],
    "Kebabpizza Special": ["kebab pizza special"],
    "Kycklingkebab Pizza": ["kyckling kebabpizza", "kyckling kebab pizza"],
    "Gyrospizza": ["gyros pizza"],
    "Vegansk Kebabpizza": ["vegansk kebab pizza"],
    "Esras": ["esras pizza"],
    "Messina": ["mesina"],
    "Rosella": ["rosela"],
    "Ceasarsallad": ["caesarsallad", "cesarsallad", "caesar sallad"],
    "Mozarellasallad": ["mozzarellasallad", "mozzarella sallad"],
    "Ost-Skinksallad": ["ost och skinksallad", "ost skink sallad"],
    "Gambretti Sallad": ["gambretti", "gamberetti sallad", "räksallad"],
    "Spaghetti Bolognese": ["spagetti bolognese"],
    "Canneloni Classico": ["cannelloni classico", "canneloni", "cannelloni"],
    "Penne Arrabbiata": ["penne arabiata", "penne arrabiata", "arrabbiata"],
    "Prosciutto di Parma": ["prosciutto parma", "proshutto di parma"],
    "Pasta Genovese": ["pasta jenovese"],
    "Pasta Pollo": ["pasta polo"],
    "Delizie Romagniola": ["delizie romagnola", "delizie romanola", "romagniola"],
    "Spaghetti Bigoli": ["spagetti bigoli", "spaghetti bigolli"],
    "Frutti di Mare": ["frutti de mare"],
    "Kebab med bröd": ["kebab i bröd"],
    "Kycklingkebab Rulle": ["kycklingkebabrulle", "kyckling kebabrulle"],
    "Kycklingkebab med bröd": ["kycklingkebab i bröd"],
    "Vegansk Kebab med bröd": ["vegansk kebab i bröd"],
    "Gyros med bröd": ["gyros i bröd"],
    "Gyrostallrik": ["jirostallrik", "gyros tallrik"],
    "Gyrosrulle": ["jirosrulle", "gyros rulle"],
    "Falafel med bröd": ["falafel i bröd"],
    "Fish and Chips": ["fish n chips", "fisk och chips"],
    "Fläskfilé Black & White": ["black and white", "black white"],
    "Fläskfilé Oscar": ["flaskfile oscar", "fläskfilé oskar"],
    "Oxfilé Pasta": ["oxfile pasta"],
    "Chicken Bits": ["chickenbits", "kycklingbitar"],
    "Hamburgare": ["vanlig hamburgare"],
}

PRICE_REVIEW_REQUIRED = {"Tropicana"}


def _key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"al-forno-{normalized}-{digest}"


def _option_group(name: str, options: Iterable[tuple[str, int, list[str]]], *, group_type: str) -> dict:
    group_key = _key(f"group:{name}")
    return {
        "source_key": group_key,
        "catalog_group_source_key": group_key,
        "name": name,
        "group_type": group_type,
        "selection_mode": "single",
        "is_required": True,
        "min_select": 1,
        "max_select": 1,
        "prerequisite_option_source_keys": [],
        "options": [
            {
                "source_key": _key(f"{name}:{option_name}"),
                "name": option_name,
                "kitchen_name": option_name,
                "price_delta_minor": price_delta * 100,
                "is_default": False,
                "aliases": aliases,
                "sort_order": index,
            }
            for index, (option_name, price_delta, aliases) in enumerate(options)
        ],
        "sort_order": 0,
    }


def _item(category: str, name: str, price: int, description: str, order: int) -> dict:
    groups = []
    if category == "Pan Pizza":
        groups.append(
            _option_group(
                "Storlek",
                (
                    ("Small", 0, ["liten"]),
                    ("Medium", 20, ["mellan"]),
                    ("Large", 100, ["stor"]),
                ),
                group_type="size",
            )
        )
    if name in SIDE_REQUIRED:
        groups.append(
            _option_group(
                "Tillbehör",
                (
                    ("Pommes", 0, ["pommes frites"]),
                    ("Ris", 0, []),
                    ("Bulgur", 0, []),
                ),
                group_type="choice",
            )
        )
    if name == "Hamburgare":
        groups.append(
            _option_group(
                "Vikt",
                (
                    ("90g", 0, ["90 gram", "90 grams"]),
                    ("150g", 10, ["150 gram", "150 grams"]),
                ),
                group_type="choice",
            )
        )

    return {
        "source_key": _key(f"item:{category}:{name}"),
        "category_source_key": _key(f"category:{category}"),
        "official_name": name,
        "customer_display_name": name,
        "kitchen_display_name": name,
        "description": description,
        "item_type": "food",
        "base_price_minor": price * 100,
        "currency": "SEK",
        "is_active": True,
        "allow_customer_notes": True,
        "sort_order": order,
        "aliases": APPROVED_ALIASES.get(name, []),
        "option_groups": groups,
        "metadata": {
            "category_name": category,
            "source_name": name,
            "price_verification_status": (
                "needs_review" if name in PRICE_REVIEW_REQUIRED else "provided_by_user"
            ),
        },
    }


def build_catalog() -> dict:
    sections = (
        ("Pizza", PIZZAS),
        ("Pan Pizza", PAN_PIZZAS),
        ("Pasta", PASTA),
        ("Sallad", SALADS),
        ("Kebab", KEBAB),
        ("Hamburgare", BURGERS),
        ("À la Carte", A_LA_CARTE),
    )
    categories = []
    items = []
    for category_order, (category, rows) in enumerate(sections):
        categories.append(
            {
                "source_key": _key(f"category:{category}"),
                "name": category,
                "description": None,
                "sort_order": category_order,
                "is_active": True,
            }
        )
        items.extend(
            _item(category, name, price, description, order)
            for order, (name, price, description) in enumerate(rows)
        )

    return {
        "schema_version": 1,
        "restaurant_id": RESTAURANT_ID,
        "restaurant_slug": RESTAURANT_SLUG,
        "currency": "SEK",
        "verification_status": "provided_by_user",
        "source": {
            "type": "user_provided_menu_cross_checked_with_official_site",
            "urls": SOURCE_URLS,
            "captured_at": "2026-09-02",
            "warning": "Confirm the final menu and special-request pricing with the restaurant before production.",
        },
        "categories": categories,
        "items": items,
    }


def build_knowledge_base(catalog: dict) -> str:
    lines = [
        "RESTAURANG AL FORNO - TESTUNDERLAG",
        "Adress: Nynäsvägen 21, 136 47 Haninge",
        "Telefon: 08 777 92 06",
        "Öppettider: måndag-söndag 09:00-21:00 enligt restaurangens webbplats.",
        "",
        "Svara bara på menyfrågor med uppgifterna nedan. Hitta aldrig på innehåll, allergener eller tillval.",
        "",
    ]
    by_category: dict[str, list[dict]] = {}
    for item in catalog["items"]:
        by_category.setdefault(item["metadata"]["category_name"], []).append(item)
    for category in (value["name"] for value in catalog["categories"]):
        lines.append(category.upper())
        for item in by_category[category]:
            price = item["base_price_minor"] // 100
            size_text = ""
            if item["option_groups"] and item["option_groups"][0]["group_type"] == "size":
                size_text = " (small 160 kr, medium 180 kr, large 260 kr)"
            lines.append(f"- {item['official_name']}: {item['description']}. {price} kr{size_text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    data_directory = Path(__file__).resolve().parent.parent / "app" / "data"
    output = data_directory / "al_forno_menu_candidate.json"
    catalog = build_catalog()
    output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_directory / "al_forno_knowledge_base.txt").write_text(
        build_knowledge_base(catalog),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
