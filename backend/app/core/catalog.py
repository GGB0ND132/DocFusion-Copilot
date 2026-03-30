from __future__ import annotations

from collections.abc import Iterable


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "GDP总量": (
        "gdp总量",
        "gdp",
        "地区生产总值",
        "地区gdp",
        "生产总值",
        "经济总量",
    ),
    "常住人口": (
        "常住人口",
        "人口规模",
        "人口数",
        "人口",
    ),
    "人均GDP": (
        "人均gdp",
        "人均 gdp",
        "人均地区生产总值",
        "人均生产总值",
        "人均国内生产总值",
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
        "日期",
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
    "大洲": (
        "大洲",
        "所在大洲",
        "洲别",
    ),
    "每日检测数": (
        "每日检测数",
        "日检测数",
        "当日检测数",
        "每日核酸检测数",
        "核酸检测量",
        "检测量",
        "日检测量",
        "单日检测量",
    ),
    "病例数": (
        "病例数",
        "新增病例",
        "新增确诊",
        "新增确诊病例",
        "新增感染病例",
    ),
    "城市": (
        "城市",
        "城市名称",
        "地市",
    ),
    "区": (
        "区",
        "区县",
        "行政区",
        "县区",
    ),
    "站点名称": (
        "站点名称",
        "站点",
        "监测点",
        "监测站",
        "点位名称",
    ),
    "空气质量指数": (
        "空气质量指数",
        "aqi",
        "空气质量指数aqi",
    ),
    "PM10监测值": (
        "pm10监测值",
        "pm10",
        "pm10浓度",
        "可吸入颗粒物",
    ),
    "PM2.5监测值": (
        "pm2.5监测值",
        "pm2.5",
        "pm2_5",
        "pm2.5浓度",
        "细颗粒物",
    ),
    "首要污染物": (
        "首要污染物",
        "主要污染物",
        "首污",
    ),
    "污染类型": (
        "污染类型",
        "污染等级",
        "空气质量等级",
        "质量等级",
    ),
}

FIELD_CANONICAL_UNITS: dict[str, str] = {
    "GDP总量": "亿元",
    "常住人口": "万人",
    "人均GDP": "元",
    "一般公共预算收入": "亿元",
    "合同金额": "元",
    "每日检测数": "万份",
    "病例数": "例",
}

FIELD_ENTITY_TYPES: dict[str, str] = {
    "GDP总量": "region",
    "常住人口": "region",
    "人均GDP": "region",
    "一般公共预算收入": "city",
    "合同金额": "contract",
    "签订日期": "contract",
    "甲方": "contract",
    "乙方": "contract",
    "大洲": "region",
    "每日检测数": "region",
    "病例数": "region",
    "城市": "air_station",
    "区": "air_station",
    "站点名称": "air_station",
    "空气质量指数": "air_station",
    "PM10监测值": "air_station",
    "PM2.5监测值": "air_station",
    "首要污染物": "air_station",
    "污染类型": "air_station",
}

ENTITY_COLUMN_ALIASES: tuple[str, ...] = (
    "国家/地区",
    "国家地区",
    "国家",
    "地区",
    "省份",
    "城市",
    "城市名称",
    "地市",
    "站点名称",
    "站点",
    "监测点",
    "监测站",
    "点位名称",
    "名称",
    "项目",
    "实体",
    "公司",
)

CITY_NAMES: tuple[str, ...] = (
    "中国",
    "北京",
    "上海",
    "天津",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "内蒙古",
    "香港",
    "澳门",
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
    "extract_and_fill_template": ("模板", "填表", "回填", "填充"),
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
