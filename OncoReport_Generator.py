# -*- coding:utf-8 -*-
"""
@coding_address: Kangan
@coding_edition: Python 3.6.8
@author: 李茂正
@date: 2026/8/5 14:19
癌症检测报告（MRD/PD-L1/HER2）LLM 自动解读流程实现

整体流程：PDF关键页文本提取 → 第一步：报告类型/指标存在性判断 → 第二步：仅对"是"的指标批量解读

设计文档：癌症报告解读方案设计.md
"""
import json
import os
import re
import time

import requests
from openai import OpenAI
from tool.mysql import Mysql

# ============================================================
# 一、配置文件（指标别名/描述/关键字段/优先级）
# ============================================================

INDICATOR_CONFIG = {
    "MRD": {
        "aliases": ["MRD", "微小残留病灶", "分子残留病灶", "ctDNA MRD"],
        "description": "用于判断肿瘤治疗后是否存在微量残留病灶，指导辅助治疗决策",
        "key_fields": ["MRD状态", "检测灵敏度", "VAF", "采样时间点"],
        "priority": "high",
    },
    "PDL1": {
        "aliases": ["PD-L1", "PDL1", "程序性死亡受体配体1"],
        "description": "免疫治疗生物标志物，关注TPS/CPS评分及判读标准",
        "key_fields": ["TPS", "CPS", "染色克隆号", "判读结果"],
        "priority": "high",
    },
    "HER2": {
        "aliases": ["HER2", "HER-2", "人表皮生长因子受体2", "ERBB2"],
        "description": "乳腺癌/胃癌靶向治疗关键标志物，关注IHC分级与FISH结果",
        "key_fields": ["IHC结果", "FISH结果", "基因拷贝数"],
        "priority": "high",
    },
}

# ============================================================
# 二、模型与文本提取参数
# ============================================================

API_KEY = "sk-c9033ccf97e74cf99d58d4f04b2d42c1"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 第一步是分类任务，可用轻量/低成本模型；第二步是生成任务，建议用能力更强的模型
STEP1_MODEL = "deepseek-v4-flash"
STEP2_MODEL = "deepseek-v4-flash"

FIRST_PAGES = 5  # 优先提取前N页
FALLBACK_PAGES = 10  # 未命中时兜底扩展的页数上限
DISCLAIMER = "本解读仅供参考，具体诊疗请以医生意见为准"


# ============================================================
# 三、PDF 文本提取策略（只取关键页/关键区间）
# ============================================================

# def extract_pdf_pages(file_path, page_end):
#     """
#     使用 pdfplumber 按页提取文本，保留基本段落/表格结构。
#     返回 (pages_text, total_pages)：pages_text 为 {页码(1起): 文本} 字典
#     """
#     pages_text = {}
#     with pdfplumber.open(file_path) as pdf:
#         total = len(pdf.pages)
#         limit = min(page_end, total)
#         for i in range(limit):
#             text = pdf.pages[i].extract_text() or ""
#             pages_text[i + 1] = text
#     return pages_text, total


# def extract_pdf(file_path, filename, MAX_LEN):
#     with open(file_path, 'rb') as file:
#         files = {'file': (filename, file, 'application/pdf')}
#         response = requests.post('http://192.168.135.233:9999/upload', files=files, timeout=600)
#         content = response.json()
#         textdata = content['textdata']
#         textdata = re.sub(r'<[^>]+>', '', textdata)
#         print("原始 textdata 长度:", len(textdata))
#         if len(textdata) >= MAX_LEN:
#             textdata = textdata[:MAX_LEN]
#         return textdata


def extract_pdf(file_path, filename, MAX_LEN, max_retries=3):
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as file:
                files = {'file': (filename, file, 'application/pdf')}
                response = requests.post(
                    'http://192.168.135.233:9999/upload',
                    files=files,
                    timeout=960
                )
                if response.status_code == 500:
                    print(response.json()['error'])
                    raise ValueError('解析超时！')
                content = response.json()
                textdata = content['textdata']
                textdata = re.sub(r'<[^>]+>', '', textdata)
                print("原始 textdata 长度:", len(textdata))
                if len(textdata) >= MAX_LEN:
                    textdata = textdata[:MAX_LEN]
                return textdata

        except requests.exceptions.ReadTimeout as e:
            print(f"第 {attempt + 1} 次尝试超时，等待后重试...")
            if attempt == max_retries - 1:
                print("已达到最大重试次数，抛出异常")
                raise e
            # 指数退避策略
            wait_time = min(2 ** attempt, 60)  # 最多等待60秒
            print(f"等待 {wait_time} 秒后重试")
            time.sleep(wait_time)

        except Exception as e:
            print(f"发生其他错误: {e}")
            raise e


def build_hit_pattern(config):
    """将所有指标的别名合并为一个用于关键词命中的正则"""
    aliases = []
    for conf in config.values():
        aliases.extend(conf.get("aliases", []))
    aliases = sorted(set(aliases), key=len, reverse=True)
    return re.compile('|'.join(re.escape(a) for a in aliases))


def combine_pages(pages_text):
    """将多页文本拼接为带页码标记的文本，便于LLM定位"""
    parts = []
    for page_no in sorted(pages_text):
        text = (pages_text[page_no] or "").strip()
        if text:
            parts.append("===== 第{}页 =====\n{}".format(page_no, text))
    return "\n\n".join(parts)


# def extract_key_text(file_path):
#     """
#     关键页文本提取策略：
#     1. 优先提取前 FIRST_PAGES 页
#     2. 若前几页中未检测到任何配置指标关键词，再追加提取中间部分（6~10页）兜底
#     返回 (key_text, total_pages, used_pages, expanded)
#     """
#     pattern = build_hit_pattern(INDICATOR_CONFIG)
#     pages_text, total = extract_pdf_pages(file_path, FIRST_PAGES)
#     expanded = False
#     used_pages = set(pages_text.keys())
#
#     first_text = combine_pages(pages_text)
#     if not pattern.search(first_text):
#         print("前{}页未命中任何指标关键词，扩展提取至第{}页兜底".format(FIRST_PAGES, FALLBACK_PAGES))
#         more_pages, total = extract_pdf_pages(file_path, FALLBACK_PAGES)
#         for page_no, text in more_pages.items():
#             if page_no not in pages_text:
#                 pages_text[page_no] = text
#                 used_pages.add(page_no)
#         expanded = True
#
#     key_text = combine_pages(pages_text)
#     print("提取到{}页文本，字符数: {}，是否扩展兜底: {}".format(len(used_pages), len(key_text), expanded))
#     return key_text, total, used_pages, expanded


# ============================================================
# 四、LLM 调用封装（JSON 强约束 + 重试）
# ============================================================

def safe_json_loads(content):
    """解析LLM返回的JSON，兼容markdown代码块包裹等情况"""
    if not content:
        return None
    content = content.strip()
    content = re.sub(r'^```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```$', '', content)
    try:
        return json.loads(content)
    except Exception:
        match = re.search(r'\{.*\}', content, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


def chat_json(system_prompt, user_prompt, model, retries=3):
    """
    调用LLM并强制返回JSON，失败重试1~2次，仍失败返回None标记人工处理
    """
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    for attempt in range(retries):
        start_time = time.time()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                temperature=0.1,
                stream=False,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            data = safe_json_loads(content)
            if data:
                print("第{}次调用成功，耗时: {:.2f}秒".format(attempt + 1, time.time() - start_time))
                return data
            print("第{}次调用返回非JSON/空内容，重试中...".format(attempt + 1))
        except Exception as e:
            print("第{}次调用失败: {}".format(attempt + 1, e))
        time.sleep(1)
    print("所有重试均失败，标记人工处理")
    return None


# ============================================================
# 五、第一步：报告类型/指标判断
# ============================================================

STEP1_SYSTEM_PROMPT = """你是肿瘤检测报告审阅助手。你的任务是判断一份检测报告是否属于某个指标的检测报告。

请严格遵守以下规则：
1. 判断的是"报告类型"，不是"关键词是否出现"。正文中仅作为既往病史/背景信息提及的指标（如"既往HER2阴性"），不算该指标的检测报告。
2. 判断依据：该指标是否有明确的本次检测结果；该指标是否有对应的检测方法学说明（如"采用IHC法检测HER2"）。
3. 一份报告可能同时是多个指标的联合报告，请对输入配置中的每个指标独立判断。
4. 排除既往病史/背景信息的干扰。
5. 只返回严格JSON，不要输出其他任何内容。

输出格式示例：
{
  "MRD": {"is_report_type": "yes", "status": "已出结果", "evidence": "报告摘要页：MRD检测结果为阳性..."},
  "PDL1": {"is_report_type": "no", "status": "仅病史提及", "evidence": "既往病理提及PD-L1阴性，非本次检测项目"}
}

字段说明：
- is_report_type：核心判断结果，取值为 yes 或 no
- status：细化状态，如"已出结果/检测中/仅病史提及/未检测"
- evidence：引用原文关键片段，便于人工复核，必须从原文中摘取，禁止编造"""


def build_step1_user_prompt(key_text, config):
    config_json = json.dumps(config, ensure_ascii=False, indent=2)
    return """请判断以下肿瘤检测报告属于哪些指标的检测报告。

【配置文件】
{}

【报告关键页文本】
{}

请针对配置中每个指标独立输出判断结果，严格按JSON格式返回。""".format(config_json, key_text[:3000])


def normalize_step1_result(raw, config):
    """将LLM返回的指标判断结果对齐到配置指标，缺失项默认 no/未检测"""
    result = {}
    for indicator, conf in config.items():
        entry = None
        for key, val in (raw or {}).items():
            if indicator == key or key in conf.get("aliases", []):
                entry = val
                break
        if isinstance(entry, dict):
            result[indicator] = {
                "is_report_type": str(entry.get("is_report_type", "no")).strip().lower(),
                "status": entry.get("status", "未检测"),
                "evidence": entry.get("evidence", ""),
            }
        else:
            result[indicator] = {"is_report_type": "no", "status": "未检测", "evidence": ""}
    return result


def judge_report_types(key_text):
    """
    第一步：报告类型/指标判断。
    返回 {指标: {is_report_type, status, evidence}}
    """
    raw = chat_json(STEP1_SYSTEM_PROMPT,
                    build_step1_user_prompt(key_text, INDICATOR_CONFIG),
                    STEP1_MODEL)
    if raw is None:
        print("第一步LLM调用失败，全部指标按未检测处理")
        raw = {}
    result = normalize_step1_result(raw, INDICATOR_CONFIG)
    for indicator, info in result.items():
        print("指标[{}] 判断结果: {}".format(indicator, info))
    return result


# ============================================================
# 六、第二步：仅对"是"的指标批量专项解读
# ============================================================

STEP2_SYSTEM_PROMPT = """你是肿瘤专项指标解读专家。你的任务是仅基于给定原文对指定的检测指标进行临床解读。

请严格遵守以下规则：
1. 仅基于给定原文解读，不得引入文本外的医学假设，严禁编造数值。
2. 若原文中某指标缺少有效数值/结果（第一步误判情况），应返回"数据不足，无法解读"，而非强行编造。
3. 每个指标必须包含 source_quote 溯源字段，引用原文中对应的关键片段。
4. 增加 confidence 字段（high/medium/low）标注解读把握度，低置信度需人工复核。
5. 只返回严格JSON，不要输出其他任何内容。

输出格式示例：
{
  "MRD": {
    "summary": "结论性解读，约150字以内",
    "key_values": {"MRD状态": "阳性", "VAF": "0.05%", "采样时间点": "术后4周"},
    "clinical_significance": "临床意义说明",
    "confidence": "high",
    "source_quote": "引用原文，用于溯源，禁止编造数值"
  }
}

字段说明：
- summary：结论性解读，每指标控制在150~200字以内
- key_values：从原文提取该指标的关键数值字段（对齐配置中的key_fields）
- clinical_significance：临床意义说明
- confidence：解读把握度，high/medium/low
- source_quote：引用原文，用于溯源，禁止编造数值"""


def build_step2_user_prompt(key_text, yes_indicators, config):
    """为每个yes指标拼接配置信息（key_fields/description）"""
    target_cfg = {}
    for indicator in yes_indicators:
        conf = config.get(indicator, {})
        target_cfg[indicator] = {
            "description": conf.get("description", ""),
            "key_fields": conf.get("key_fields", []),
        }
    target_json = json.dumps(target_cfg, ensure_ascii=False, indent=2)
    return """请对下列指标进行专项解读（仅基于给定原文，禁止编造数值）。

【本次需要解读的指标及关键字段】
{}

【报告关键页文本】
{}

请对上述每个指标输出解读结果，严格按JSON格式返回，每个指标必须包含source_quote溯源。""".format(target_json, key_text)


def normalize_step2_result(raw, yes_indicators):
    """将LLM返回的解读结果对齐到yes指标，缺失项标记为数据不足"""
    result = {}
    for indicator in yes_indicators:
        entry = None
        for key, val in (raw or {}).items():
            if indicator == key or key in INDICATOR_CONFIG.get(indicator, {}).get("aliases", []):
                entry = val
                break
        if isinstance(entry, dict) and (entry.get("summary") or entry.get("source_quote")):
            result[indicator] = {
                "summary": entry.get("summary", ""),
                "key_values": entry.get("key_values", {}) if isinstance(entry.get("key_values"), dict) else {},
                "clinical_significance": entry.get("clinical_significance", ""),
                "confidence": entry.get("confidence", "low"),
                "source_quote": entry.get("source_quote", ""),
            }
        else:
            result[indicator] = {
                "summary": "数据不足，无法解读",
                "key_values": {},
                "clinical_significance": "",
                "confidence": "low",
                "source_quote": "",
            }
    return result


def interpret_indicators(key_text, yes_indicators):
    """
    第二步：仅对第一步判定为yes的指标批量解读（一次调用，可联合解读）。
    返回 {指标: {summary, key_values, clinical_significance, confidence, source_quote}}
    """
    if not yes_indicators:
        return {}
    raw = chat_json(STEP2_SYSTEM_PROMPT,
                    build_step2_user_prompt(key_text, yes_indicators, INDICATOR_CONFIG),
                    STEP2_MODEL)
    if raw is None:
        print("第二步LLM调用失败，全部标记为数据不足")
        raw = {}
    result = normalize_step2_result(raw, yes_indicators)
    for indicator, info in result.items():
        print("指标[{}] 解读完成: confidence={}".format(indicator, info.get("confidence")))
    return result


# ============================================================
# 七、渲染最终解读报告
# ============================================================

def render_report(step1_result, step2_result):
    yes_indicators = [k for k, v in step1_result.items() if v.get("is_report_type") == "yes"]
    lines = []
    lines.append("=" * 40)
    lines.append("癌症检测报告 LLM 自动解读结果")
    lines.append("=" * 40)
    # lines.append("报告页数: {}页 | 实际提取: {}页 | 兜底扩展: {}".format(total_pages, len(used_pages), expanded))

    if not yes_indicators:
        lines.append("本报告不包含配置指标，无需解读")
        return "\n".join(lines)

    for indicator in yes_indicators:
        info = step1_result[indicator]
        interp = step2_result.get(indicator, {})
        lines.append("\n" + "-" * 40)
        lines.append("【{}】 状态: {}".format(indicator, info.get("status")))
        lines.append("-" * 40)
        lines.append("结论: {}".format(interp.get("summary", "数据不足，无法解读")))
        if interp.get("key_values"):
            lines.append("关键数值:")
            for field, value in interp["key_values"].items():
                lines.append("  - {}: {}".format(field, value))
        if interp.get("clinical_significance"):
            lines.append("临床意义: {}".format(interp["clinical_significance"]))
        lines.append("把握度: {}".format(interp.get("confidence", "low")))
        if interp.get("source_quote"):
            lines.append("原文溯源: {}".format(interp["source_quote"]))

    lines.append("\n" + "=" * 40)
    lines.append("{}".format(DISCLAIMER))
    return "\n".join(lines)


# ============================================================
# 八、主流程
# ============================================================

def generate_report(filename):
    UPLOAD_DIR = "/home/node9/xg/pdf_parse/uploads"
    DB_CONF_FILE = 'conf/db.smartonco_4.conf'
    mysql = Mysql(db_conf_file=DB_CONF_FILE)
    MAX_LEN = 57344
    """
    癌症报告解读完整流程：
    1. PDF关键页文本提取（未命中则兜底扩展）
    2. 第一步：报告类型/指标判断
    3. 过滤 is_report_type == "yes" 的指标
    4. 第二步：批量专项解读（仅针对yes指标）
    5. 渲染最终解读报告
    返回 (解读报告文本, 结构化结果)
    """
    print("开始处理文件: {}".format(filename))
    start_time = time.time()
    # 1. 构建文件路径
    file_path = os.path.join(UPLOAD_DIR, filename)
    # 1. 提取关键页文本
    # key_text, total_pages, used_pages, expanded = extract_key_text(pdf_path)
    # if not key_text:
    #     print("PDF关键页提取为空，标记人工处理")
    #     return "PDF关键页文本提取为空，无法解读，请人工处理", {}
    key_text = extract_pdf(file_path, filename, MAX_LEN)

    # 2. 第一步：报告类型判断
    step1_result = judge_report_types(key_text)

    # 3. 过滤yes指标
    yes_indicators = [k for k, v in step1_result.items() if v.get("is_report_type") == "yes"]
    print("判定为该指标检测报告的指标: {}".format(yes_indicators))

    # 4. 第二步：批量解读
    step2_result = interpret_indicators(key_text, yes_indicators)

    # 5. 渲染
    report = render_report(step1_result, step2_result)
    print("总耗时: {:.2f}秒".format(time.time() - start_time))

    structured = {
        "step1": step1_result,
        "step2": step2_result,
        "yes_indicators": yes_indicators,
    }
    return report, structured


if __name__ == '__main__':
    pdf_file = 'Lynch综合征风险评估（健康人）.pdf'
    report_text, structured_result = generate_report(pdf_file)
    print(report_text)
    print(structured_result)
