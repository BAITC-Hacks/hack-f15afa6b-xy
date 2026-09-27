"""Official Kazakhstan cities from KATO NK RK 11-2025, updated 2026-09-18."""
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException, Query

REGION_NAMES = {
    "KZ-ABA": "область Абай", "KZ-AKM": "Акмолинская область",
    "KZ-AKT": "Актюбинская область", "KZ-ALM": "Алматинская область",
    "KZ-ATY": "Атырауская область", "KZ-ZKO": "Западно-Казахстанская область",
    "KZ-ZHA": "Жамбылская область", "KZ-ZHE": "область Жетісу",
    "KZ-KAR": "Карагандинская область", "KZ-KOS": "Костанайская область",
    "KZ-KZY": "Кызылординская область", "KZ-MAN": "Мангистауская область",
    "KZ-PAV": "Павлодарская область", "KZ-SEV": "Северо-Казахстанская область",
    "KZ-TUR": "Туркестанская область", "KZ-ULY": "область Ұлытау",
    "KZ-VKO": "Восточно-Казахстанская область", "KZ-AST": "город Астана",
    "KZ-ALA": "город Алматы", "KZ-SHY": "город Шымкент",
}

CITIES = [
    {'code': '101010000', 'name_ru': 'Семей', 'name_kk': 'Семей', 'region_id': 'KZ-ABA'},
    {'code': '101810000', 'name_ru': 'Курчатов', 'name_kk': 'Курчатов', 'region_id': 'KZ-ABA'},
    {'code': '103620100', 'name_ru': 'Аягоз', 'name_kk': 'Аягөз', 'region_id': 'KZ-ABA'},
    {'code': '104221100', 'name_ru': 'Шар', 'name_kk': 'Шар', 'region_id': 'KZ-ABA'},
    {'code': '111010000', 'name_ru': 'Кокшетау', 'name_kk': 'Көкшетау', 'region_id': 'KZ-AKM'},
    {'code': '111610000', 'name_ru': 'Косшы', 'name_kk': 'Қосшы', 'region_id': 'KZ-AKM'},
    {'code': '111810000', 'name_ru': 'Степногорск', 'name_kk': 'Степногор', 'region_id': 'KZ-AKM'},
    {'code': '113220100', 'name_ru': 'Акколь', 'name_kk': 'Ақкөл', 'region_id': 'KZ-AKM'},
    {'code': '113820100', 'name_ru': 'Атбасар', 'name_kk': 'Атбасар', 'region_id': 'KZ-AKM'},
    {'code': '114020100', 'name_ru': 'Макинск', 'name_kk': 'Макинск', 'region_id': 'KZ-AKM'},
    {'code': '114520100', 'name_ru': 'Степняк', 'name_kk': 'Степняк', 'region_id': 'KZ-AKM'},
    {'code': '114620100', 'name_ru': 'Ерейментау', 'name_kk': 'Ерейментау', 'region_id': 'KZ-AKM'},
    {'code': '114820100', 'name_ru': 'Есиль', 'name_kk': 'Есіл', 'region_id': 'KZ-AKM'},
    {'code': '115420100', 'name_ru': 'Державинск', 'name_kk': 'Державин', 'region_id': 'KZ-AKM'},
    {'code': '117020100', 'name_ru': 'Щучинск', 'name_kk': 'Щучинск', 'region_id': 'KZ-AKM'},
    {'code': '151010000', 'name_ru': 'Актобе', 'name_kk': 'Ақтөбе', 'region_id': 'KZ-AKT'},
    {'code': '153220100', 'name_ru': 'Алга', 'name_kk': 'Алға', 'region_id': 'KZ-AKT'},
    {'code': '154820100', 'name_ru': 'Кандыагаш', 'name_kk': 'Қандыағаш', 'region_id': 'KZ-AKT'},
    {'code': '154823100', 'name_ru': 'Эмба', 'name_kk': 'Ембі', 'region_id': 'KZ-AKT'},
    {'code': '154825100', 'name_ru': 'Жем', 'name_kk': 'Жем', 'region_id': 'KZ-AKT'},
    {'code': '155621100', 'name_ru': 'Темир', 'name_kk': 'Темір', 'region_id': 'KZ-AKT'},
    {'code': '156020100', 'name_ru': 'Хромтау', 'name_kk': 'Хромтау', 'region_id': 'KZ-AKT'},
    {'code': '156420100', 'name_ru': 'Шалкар', 'name_kk': 'Шалқар', 'region_id': 'KZ-AKT'},
    {'code': '191010000', 'name_ru': 'Қонаев', 'name_kk': 'Қонаев', 'region_id': 'KZ-ALM'},
    {'code': '191810000', 'name_ru': 'Алатау', 'name_kk': 'Алатау', 'region_id': 'KZ-ALM'},
    {'code': '194020100', 'name_ru': 'Есик', 'name_kk': 'Есік', 'region_id': 'KZ-ALM'},
    {'code': '195220100', 'name_ru': 'Каскелен', 'name_kk': 'Қаскелең', 'region_id': 'KZ-ALM'},
    {'code': '196220100', 'name_ru': 'Талгар', 'name_kk': 'Талғар', 'region_id': 'KZ-ALM'},
    {'code': '231010000', 'name_ru': 'Атырау', 'name_kk': 'Атырау', 'region_id': 'KZ-ATY'},
    {'code': '233620100', 'name_ru': 'Кульсары', 'name_kk': 'Құлсары', 'region_id': 'KZ-ATY'},
    {'code': '271010000', 'name_ru': 'Уральск', 'name_kk': 'Орал', 'region_id': 'KZ-ZKO'},
    {'code': '273620100', 'name_ru': 'Аксай', 'name_kk': 'Ақсай', 'region_id': 'KZ-ZKO'},
    {'code': '311010000', 'name_ru': 'Тараз', 'name_kk': 'Тараз', 'region_id': 'KZ-ZHA'},
    {'code': '316020100', 'name_ru': 'Жанатас', 'name_kk': 'Жаңатас', 'region_id': 'KZ-ZHA'},
    {'code': '316220100', 'name_ru': 'Каратау', 'name_kk': 'Қаратау', 'region_id': 'KZ-ZHA'},
    {'code': '316621100', 'name_ru': 'Шу', 'name_kk': 'Шу', 'region_id': 'KZ-ZHA'},
    {'code': '331010000', 'name_ru': 'Талдыкорган', 'name_kk': 'Талдықорған', 'region_id': 'KZ-ZHE'},
    {'code': '331810000', 'name_ru': 'Текели', 'name_kk': 'Текелі', 'region_id': 'KZ-ZHE'},
    {'code': '333420100', 'name_ru': 'Ушарал', 'name_kk': 'Үшарал', 'region_id': 'KZ-ZHE'},
    {'code': '334420100', 'name_ru': 'Уштобе', 'name_kk': 'Үштөбе', 'region_id': 'KZ-ZHE'},
    {'code': '334620100', 'name_ru': 'Жаркент', 'name_kk': 'Жаркент', 'region_id': 'KZ-ZHE'},
    {'code': '334820100', 'name_ru': 'Саркан', 'name_kk': 'Сарқан', 'region_id': 'KZ-ZHE'},
    {'code': '351010000', 'name_ru': 'Караганда', 'name_kk': 'Қарағанды', 'region_id': 'KZ-KAR'},
    {'code': '351610000', 'name_ru': 'Балхаш', 'name_kk': 'Балқаш', 'region_id': 'KZ-KAR'},
    {'code': '352110000', 'name_ru': 'Приозерск', 'name_kk': 'Приозерск', 'region_id': 'KZ-KAR'},
    {'code': '352210000', 'name_ru': 'Сарань', 'name_kk': 'Саран', 'region_id': 'KZ-KAR'},
    {'code': '352410000', 'name_ru': 'Темиртау', 'name_kk': 'Теміртау', 'region_id': 'KZ-KAR'},
    {'code': '352810000', 'name_ru': 'Шахтинск', 'name_kk': 'Шахтинск', 'region_id': 'KZ-KAR'},
    {'code': '353220100', 'name_ru': 'Абай', 'name_kk': 'Абай', 'region_id': 'KZ-KAR'},
    {'code': '354820100', 'name_ru': 'Каркаралинск', 'name_kk': 'Қарқаралы', 'region_id': 'KZ-KAR'},
    {'code': '391010000', 'name_ru': 'Костанай', 'name_kk': 'Қостанай', 'region_id': 'KZ-KOS'},
    {'code': '391610000', 'name_ru': 'Аркалык', 'name_kk': 'Арқалық', 'region_id': 'KZ-KOS'},
    {'code': '392010000', 'name_ru': 'Лисаковск', 'name_kk': 'Лисаков', 'region_id': 'KZ-KOS'},
    {'code': '392410000', 'name_ru': 'Рудный', 'name_kk': 'Рудный', 'region_id': 'KZ-KOS'},
    {'code': '394420100', 'name_ru': 'Житикара', 'name_kk': 'Жітіқара', 'region_id': 'KZ-KOS'},
    {'code': '395420100', 'name_ru': 'Тобыл', 'name_kk': 'Тобыл', 'region_id': 'KZ-KOS'},
    {'code': '431010000', 'name_ru': 'Кызылорда', 'name_kk': 'Қызылорда', 'region_id': 'KZ-KZY'},
    {'code': '431910000', 'name_ru': 'Байконыр', 'name_kk': 'Байқоңыр', 'region_id': 'KZ-KZY'},
    {'code': '433220100', 'name_ru': 'Аральск', 'name_kk': 'Арал', 'region_id': 'KZ-KZY'},
    {'code': '434423100', 'name_ru': 'Казалинск', 'name_kk': 'Қазалы', 'region_id': 'KZ-KZY'},
    {'code': '471010000', 'name_ru': 'Актау', 'name_kk': 'Ақтау', 'region_id': 'KZ-MAN'},
    {'code': '471810000', 'name_ru': 'Жанаозен', 'name_kk': 'Жаңаөзен', 'region_id': 'KZ-MAN'},
    {'code': '475220100', 'name_ru': 'Форт-Шевченко', 'name_kk': 'Форт-Шевченко', 'region_id': 'KZ-MAN'},
    {'code': '551010000', 'name_ru': 'Павлодар', 'name_kk': 'Павлодар', 'region_id': 'KZ-PAV'},
    {'code': '551610000', 'name_ru': 'Аксу', 'name_kk': 'Ақсу', 'region_id': 'KZ-PAV'},
    {'code': '552210000', 'name_ru': 'Экибастуз', 'name_kk': 'Екібастұз', 'region_id': 'KZ-PAV'},
    {'code': '591010000', 'name_ru': 'Петропавловск', 'name_kk': 'Петропавл', 'region_id': 'KZ-SEV'},
    {'code': '593620100', 'name_ru': 'Булаево', 'name_kk': 'Булаев', 'region_id': 'KZ-SEV'},
    {'code': '595220100', 'name_ru': 'Мамлютка', 'name_kk': 'Мамлют', 'region_id': 'KZ-SEV'},
    {'code': '595620100', 'name_ru': 'Сергеевка', 'name_kk': 'Сергеев', 'region_id': 'KZ-SEV'},
    {'code': '596020100', 'name_ru': 'Тайынша', 'name_kk': 'Тайынша', 'region_id': 'KZ-SEV'},
    {'code': '611010000', 'name_ru': 'Туркестан', 'name_kk': 'Түркістан', 'region_id': 'KZ-TUR'},
    {'code': '611610000', 'name_ru': 'Арысь', 'name_kk': 'Арыс', 'region_id': 'KZ-TUR'},
    {'code': '612010000', 'name_ru': 'Кентау', 'name_kk': 'Кентау', 'region_id': 'KZ-TUR'},
    {'code': '613820100', 'name_ru': 'Жетысай', 'name_kk': 'Жетісай', 'region_id': 'KZ-TUR'},
    {'code': '615420100', 'name_ru': 'Сарыагаш', 'name_kk': 'Сарыағаш', 'region_id': 'KZ-TUR'},
    {'code': '615820100', 'name_ru': 'Ленгер', 'name_kk': 'Леңгір', 'region_id': 'KZ-TUR'},
    {'code': '616420100', 'name_ru': 'Шардара', 'name_kk': 'Шардара', 'region_id': 'KZ-TUR'},
    {'code': '621010000', 'name_ru': 'Жезказган', 'name_kk': 'Жезқазған', 'region_id': 'KZ-ULY'},
    {'code': '621810000', 'name_ru': 'Каражал', 'name_kk': 'Қаражал', 'region_id': 'KZ-ULY'},
    {'code': '622010000', 'name_ru': 'Сатпаев', 'name_kk': 'Сәтбаев', 'region_id': 'KZ-ULY'},
    {'code': '631010000', 'name_ru': 'Усть-Каменогорск', 'name_kk': 'Өскемен', 'region_id': 'KZ-VKO'},
    {'code': '632410000', 'name_ru': 'Риддер', 'name_kk': 'Риддер', 'region_id': 'KZ-VKO'},
    {'code': '634620100', 'name_ru': 'Зайсан', 'name_kk': 'Зайсан', 'region_id': 'KZ-VKO'},
    {'code': '634820100', 'name_ru': 'Алтай', 'name_kk': 'Алтай', 'region_id': 'KZ-VKO'},
    {'code': '634821100', 'name_ru': 'Серебрянск', 'name_kk': 'Серебрянск', 'region_id': 'KZ-VKO'},
    {'code': '636820100', 'name_ru': 'Шемонаиха', 'name_kk': 'Шемонайха', 'region_id': 'KZ-VKO'},
    {'code': '710000000', 'name_ru': 'Астана', 'name_kk': 'Астана', 'region_id': 'KZ-AST'},
    {'code': '750000000', 'name_ru': 'Алматы', 'name_kk': 'Алматы', 'region_id': 'KZ-ALA'},
    {'code': '790000000', 'name_ru': 'Шымкент', 'name_kk': 'Шымкент', 'region_id': 'KZ-SHY'},
]

CITY_BY_CODE = {city["code"]: city for city in CITIES}

CITY_MAP_PRESETS = {
    "750000000": {"center": [76.945, 43.238], "bounds": [[76.55, 42.95], [77.45, 43.55]]},
    "710000000": {"center": [71.4304, 51.1282], "bounds": [[70.9, 50.8], [72.0, 51.4]]},
    "790000000": {"center": [69.5901, 42.3417], "bounds": [[69.2, 42.1], [70.0, 42.6]]},
}


def normalized_address_query(value: str, city: str = "Алматы") -> str:
    clean = " ".join(value.strip().split())
    microdistrict = re.fullmatch(
        r"(?:(?:мкр|микрорайон)\.?\s*)?(.+?[-\s]\d+)(?:\s*,\s*|\s+)(?:(?:дом|д|үй)\.?\s*)?(\d+[A-Za-zА-Яа-я/-]*)",
        clean,
        re.IGNORECASE,
    )
    if microdistrict:
        clean = f"микрорайон {microdistrict.group(1)}, {microdistrict.group(2)}"
    return clean if city.casefold() in clean.casefold() else clean + ", " + city


def attach_city_routes(router):
    city_map_cache = {code: dict(value) for code, value in CITY_MAP_PRESETS.items()}
    address_cache = {}
    request_lock = threading.Lock()
    last_request_at = [0.0]

    def search(params):
        with request_lock:
            delay = 1 - (time.monotonic() - last_request_at[0])
            if delay > 0:
                time.sleep(delay)
            base_url = os.environ.get("P109_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
            request = Request(base_url + "?" + urlencode(params), headers={
                "User-Agent": "Pulse109/0.1 (+https://github.com/Eliasans02/pulse109)",
                "Accept": "application/json",
            })
            try:
                with urlopen(request, timeout=6) as response:
                    return json.load(response)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
                raise HTTPException(503, "Поиск по карте временно недоступен. Выберите точку вручную.") from error
            finally:
                last_request_at[0] = time.monotonic()

    def resolve_city_map(city_code):
        city = CITY_BY_CODE.get(city_code)
        if not city:
            raise HTTPException(422, "Выберите город из списка")
        if city_code in city_map_cache:
            return {**city, **city_map_cache[city_code]}
        region = REGION_NAMES[city["region_id"]]
        query = f"{city['name_ru']}, {region}, Казахстан"
        payload = search({"q": query, "format": "jsonv2", "limit": 3, "countrycodes": "kz",
                          "addressdetails": 1, "accept-language": "ru,kk"})
        for result in payload if isinstance(payload, list) else []:
            try:
                latitude, longitude = float(result["lat"]), float(result["lon"])
                south, north, west, east = [float(value) for value in result.get("boundingbox", [])]
            except (KeyError, TypeError, ValueError):
                continue
            if not (40.5 <= latitude <= 55.5 and 46 <= longitude <= 88):
                continue
            if north - south > 2.5 or east - west > 3.5:
                south, north, west, east = latitude - .2, latitude + .2, longitude - .25, longitude + .25
            lat_pad, lon_pad = max((north - south) * .15, .025), max((east - west) * .15, .025)
            item = {"center": [longitude, latitude],
                    "bounds": [[west - lon_pad, south - lat_pad], [east + lon_pad, north + lat_pad]]}
            city_map_cache[city_code] = item
            return {**city, **item}
        raise HTTPException(503, "Не удалось загрузить границы выбранного города")

    @router.get("/cities")
    def cities():
        return {"items": CITIES, "count": len(CITIES), "source": "KATO NK RK 11-2025", "updated_at": "2026-09-18"}

    @router.get("/city-map")
    def city_map(city_code: str = Query(min_length=9, max_length=9)):
        return resolve_city_map(city_code)

    @router.get("/geocode")
    def geocode(q: str = Query(min_length=3, max_length=200), city_code: str = "750000000"):
        city = CITY_BY_CODE.get(city_code)
        if not city:
            raise HTTPException(422, "Выберите город из списка")
        query = normalized_address_query(q, city["name_ru"])
        key = city_code + ":" + query.casefold()
        if key in address_cache:
            return {"query": query, "items": address_cache[key], "cached": True}
        scope = resolve_city_map(city_code)
        (west, south), (east, north) = scope["bounds"]
        payload = search({"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "kz",
                          "viewbox": f"{west},{south},{east},{north}", "bounded": 1,
                          "addressdetails": 1, "accept-language": "ru,kk"})
        items = []
        for result in payload if isinstance(payload, list) else []:
            try:
                latitude, longitude = float(result["lat"]), float(result["lon"])
                bounds = [float(value) for value in result.get("boundingbox", [])]
            except (KeyError, TypeError, ValueError):
                continue
            if south <= latitude <= north and west <= longitude <= east:
                address = result.get("address") if isinstance(result.get("address"), dict) else {}
                district = next((address.get(name) for name in ("city_district", "borough", "district")
                                 if address.get(name)), None)
                if district:
                    district = re.sub(r"^(?:район\s+)|(?:\s+район)$", "", str(district), flags=re.IGNORECASE)
                items.append({"label": str(result.get("display_name") or query)[:300],
                              "latitude": latitude, "longitude": longitude, "district": district,
                              "bounds": bounds if len(bounds) == 4 else None})
        address_cache[key] = items
        return {"query": query, "items": items, "cached": False}
