from __future__ import annotations

from collections.abc import Iterable


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "GDP总量": (
        "gdp总量",
        "gdp",
        "地区生产总值",
        "生产总值",
        "经济总量",
    ),
    "常住人口": (
        "常住人口",
        "人口规模",
        "人口数",
    ),
    "人均GDP": (
        "人均gdp",
        "人均地区生产总值",
        "人均生产总值",
    ),
    "一般公共预算收入": (
        "一般公共预算收入",
        "公共预算收入",
        "一般预算收入",
        "财政收入",
    ),
    "合同金额": (
        "合同金额",
        "合同价款",
        "签约金额",
        "总金额",
    ),
    "签订日期": (
        "签订日期",
        "签约日期",
        "合同日期",
    ),
    "甲方": (
        "甲方",
        "采购方",
        "委托方",
    ),
    "乙方": (
        "乙方",
        "供应商",
        "承接方",
        "服务方",
    ),
    # ── COVID-19 dataset fields ──
    "病例数": ("病例数", "确诊", "确诊数", "确诊病例", "cases", "confirmed"),
    "死亡数": ("死亡数", "死亡", "死亡病例", "deaths"),
    "治愈数": ("治愈数", "治愈", "治愈病例", "recovered"),
    "每日检测数": ("每日检测数", "检测数", "检测", "每日检测", "tests", "daily_tests"),
    # ── Air quality dataset fields ──
    "AQI": ("AQI", "aqi", "空气质量指数"),
    "PM2.5": ("PM2.5", "pm2.5", "PM25", "细颗粒物"),
    "PM10": ("PM10", "pm10", "可吸入颗粒物"),
    "SO2": ("SO2", "so2", "二氧化硫"),
    "NO2": ("NO2", "no2", "二氧化氮"),
    "CO": ("CO", "co", "一氧化碳"),
    "O3": ("O3", "o3", "臭氧"),
}

FIELD_CANONICAL_UNITS: dict[str, str] = {
    "GDP总量": "亿元",
    "常住人口": "万人",
    "人均GDP": "元",
    "一般公共预算收入": "亿元",
    "合同金额": "元",
    "AQI": "",
    "PM2.5": "μg/m³",
    "PM10": "μg/m³",
    "SO2": "μg/m³",
    "NO2": "μg/m³",
    "CO": "mg/m³",
    "O3": "μg/m³",
}

FIELD_ENTITY_TYPES: dict[str, str] = {
    "GDP总量": "city",
    "常住人口": "city",
    "人均GDP": "city",
    "一般公共预算收入": "city",
    "合同金额": "contract",
    "签订日期": "contract",
    "甲方": "contract",
    "乙方": "contract",
    "病例数": "country",
    "死亡数": "country",
    "治愈数": "country",
    "每日检测数": "country",
    "AQI": "city",
    "PM2.5": "city",
    "PM10": "city",
    "SO2": "city",
    "NO2": "city",
    "CO": "city",
    "O3": "city",
}

ENTITY_COLUMN_ALIASES: tuple[str, ...] = (
    "城市",
    "城市名称",
    "城市名",
    "地区",
    "区域",
    "省份",
    "名称",
    "项目",
    "实体",
    "公司",
    "国家",
    "国家/地区",
    "country",
    "country/region",
    "region",
    "province/state",
    "站点",
    "监测点位",
)

DATE_COLUMN_ALIASES: tuple[str, ...] = (
    "日期",
    "date",
    "时间",
    "监测时间",
    "日期时间",
    "datetime",
    "序号",
    "编号",
)

CITY_NAMES: tuple[str, ...] = (
    "北京",
    "上海",
    "天津",
    "重庆",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "武汉",
    "西安",
    "长沙",
    "郑州",
    "青岛",
    "宁波",
    "佛山",
    "东莞",
    "厦门",
    "无锡",
)

INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "extract_and_fill_template": ("模板", "填表", "回填", "填充", "智能填", "自动填"),
    "extract_facts": ("提取", "抽取", "识别", "入库"),
    "query_facts": ("查询", "汇总", "统计", "列出"),
    "trace_fact": ("追溯", "来源", "证据"),
    "edit_document": ("编辑", "修改", "替换", "改成", "改为"),
    "summarize_document": ("摘要", "总结", "概述"),
    "reformat_document": ("排版", "格式", "整理", "规范", "重排", "清理"),
}


def iter_all_field_aliases() -> Iterable[tuple[str, str]]:
    """遍历标准字段名及其全部别名。    Yield canonical field names paired with each supported alias."""

    for canonical_name, aliases in FIELD_ALIASES.items():
        yield canonical_name, canonical_name
        for alias in aliases:
            yield canonical_name, alias
