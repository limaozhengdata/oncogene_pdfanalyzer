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
    "BRAF_V600E": {
        "aliases": ["BRAF", "BRAF V600E", "BRAF exon15 V600E", "BRAF突变"],
        "description": "甲状腺癌、黑色素瘤等肿瘤核心驱动基因，提示肿瘤侵袭风险，指导BRAF靶向药选用",
        "key_fields": ["检测结果", "检测技术", "变异位点", "肿瘤类型"],
        "priority": "high",
    },
    "BRCA": {
        "aliases": ["BRCA", "BRCA1", "BRCA2", "BRCA1/2", "BRCA基因突变"],
        "description": "遗传性乳腺癌、卵巢癌核心易感基因，用于肿瘤遗传风险评估，指导PARP抑制剂靶向用药",
        "key_fields": ["变异位点", "氨基酸变异", "突变功能", "ACMG临床意义分类", "合子类型"],
        "priority": "high",
    },
    "CLDN18_2": {
        "aliases": ["Claudin18.2", "CLDN18.2", "claudin 18.2"],
        "description": "胃癌、胃食管结合部腺癌特异性靶点蛋白，IHC检测表达水平指导Claudin18.2单抗等靶向药物使用",
        "key_fields": ["IHC染色强度", "阳性细胞占比", "检测方法", "样本质控结果"],
        "priority": "high",
    },
    "DPYD": {
            "aliases": ["DPYD", "DPD基因", "二氢嘧啶脱氢酶基因"],
            "description": "氟嘧啶类化疗药（5-FU、卡培他滨）安全用药核心代谢基因，评估DPD酶活性，预测严重骨髓抑制、腹泻等药物毒性风险，指导化疗剂量调整",
            "key_fields": ["检测rs位点", "基因型结果", "Activity Score(AS)", "代谢表型", "剂量调整建议"],
            "priority": "high",
    },
    "EGFR": {
    "aliases": ["EGFR", "表皮生长因子受体", "EGFR突变", "EGFR 5位点", "EGFR超高灵敏度检测"],
    "description": "非小细胞肺癌核心驱动基因，检测19del、L858R、T790M、G719S、L861Q等热点突变，预判一/二/三代EGFR-TKI靶向药敏感性与耐药情况，液体/组织dPCR高灵敏检测可用于血浆微量突变监测",
    "key_fields": ["外显子变异位点", "突变拷贝数", "VAF", "检测方法(dPCR/NGS)", "对应靶向药物", "证据等级", "肿瘤分型"],
    "priority": "high",
    },
    "FRα": {
        "aliases": ["FRα", "叶酸受体α", "叶酸受体Alpha"],
        "description": "卵巢高级别浆液性癌等肿瘤靶向标志物，采用IHC检测各强度阳性细胞占比，指导叶酸受体靶向药物使用",
        "key_fields": ["IHC各分级阳性比例", "肿瘤细胞含量", "检测方法", "镜下病理描述"],
        "priority": "high",
    },
    "HRR_HRD": {
        "aliases": ["HRR", "HRD", "同源重组修复", "同源重组缺陷", "HRR+HRD"],
        "description": "卵巢癌、乳腺癌、前列腺癌等PARP抑制剂核心疗效标志物，包含HRR通路基因变异+HRD基因组不稳定评分（LOH/TAI/LST），评估铂类、PARP抑制剂获益及遗传性肿瘤风险",
        "key_fields": ["HRR通路变异结果", "LOH分值", "TAI分值", "LST分值", "HRD总分", "HRD状态", "对应PARP药物证据等级"],
        "priority": "high",
    },
    "KIT_PDGFRA": {
        "aliases": ["KIT", "PDGFRA", "c-KIT", "胃肠间质瘤靶点", "KIT+PDGFRA"],
        "description": "胃肠间质瘤(GIST)核心驱动基因，检测KIT exon9/11/13/17、PDGFRA exon12/14/18热点突变，指导伊马替尼、舒尼替尼、瑞派替尼等多靶点TKI用药及预后判断",
        "key_fields": ["KIT各外显子突变结果", "PDGFRA各外显子突变结果", "突变氨基酸位点", "对应靶向药物", "证据等级"],
        "priority": "high",
    },
    "LYNCH": {
        "aliases": ["Lynch综合征", "林奇综合征", "HNPCC", "遗传性非息肉病性结直肠癌", "MMR错配修复基因"],
        "description": "遗传性结直肠癌易感综合征，检测MLH1/MSH2/MSH6/PMS2/EPCAM错配修复胚系突变，评估结直肠、子宫内膜、胃、肝胆等多癌种遗传风险，制定定期筛查方案，指导免疫治疗用药选择",
        "key_fields": ["检测基因", "核苷酸变异", "氨基酸变异", "杂合/纯合状态", "ACMG变异分级", "肿瘤风险提示", "筛查建议"],
        "priority": "high",
    },
    "MMR_PROTEIN": {
        "aliases": ["MMR蛋白", "错配修复蛋白", "MLH1 MSH2 MSH6 PMS2", "dMMR pMMR"],
        "description": "免疫组化检测4种错配修复蛋白表达，判读dMMR/pMMR，预测PD-1/PD-L1免疫药物疗效、辅助筛查Lynch综合征",
        "key_fields": ["MLH1表达", "MSH2表达", "MSH6表达", "PMS2表达", "最终分型(dMMR/pMMR)", "肿瘤细胞质控"],
        "priority": "high",
    },
    "MSI": {
        "aliases": ["MSI", "微卫星不稳定性", "MSI-H MSI-L MSS", "微卫星检测"],
        "description": "荧光PCR毛细管电泳检测BAT25/BAT26/D2S123/D5S346/D17S250五个微卫星位点，分为MSI-H/MSI-L/MSS，用于结直肠癌预后、免疫用药指导、林奇初筛",
        "key_fields": ["5个微卫星位点结果", "不稳定位点数量", "MSI分型", "用药提示", "遗传风险提示"],
        "priority": "high",
    },
    "RAS_BRAF": {
        "aliases": ["RAS家族", "KRAS NRAS BRAF", "结直肠癌RAS套餐", "RAS突变检测"],
        "description": "结直肠癌核心用药标志物，检测KRAS exon2/3/4、NRAS exon2/3/4、BRAF V600热点突变，判断西妥昔/帕尼单抗耐药，指导化疗、抗血管、免疫及联合方案选择",
        "key_fields": ["KRAS各外显子突变结果", "NRAS各外显子突变结果", "BRAF V600状态", "对应靶向/化疗药物", "证据等级", "结直肠癌用药分层"],
        "priority": "high",
    },
    "RNA_FUSION": {
        "aliases": ["RNA融合基因", "基因融合", "肉瘤融合套餐", "转录本融合检测"],
        "description": "RNA捕获高通量测序检测4000+基因融合（EWSR1、ALK、ROS1、NTRK、FGFR等），用于肉瘤、肺癌等实体瘤分子分型，筛选拉罗替尼、克唑替尼等融合靶向药适用人群",
        "key_fields": ["5'融合基因", "3'融合基因", "融合断点位置", "融合转录本", "对应靶向药物", "肿瘤分型提示"],
        "priority": "high",
    },
    "ONCOTYPE21": {
        "aliases": ["乳腺癌21基因", "Oncotype DX", "21基因复发风险评分", "RS评分"],
        "description": "HR阳性HER2阴性早期乳腺癌预后多基因表达谱检测，检测增殖、激素、HER2等21个基因mRNA表达，计算复发分数(RS)，区分患者是否能从辅助化疗获益，指导内分泌±化疗方案选择",
        "key_fields": ["21基因各CT值", "复发分数RS", "淋巴结状态", "绝经状态", "化疗获益提示", "临床分层建议"],
        "priority": "high",
    },
    "HRD_SCORE": {
        "aliases": ["HRD评分", "同源重组缺陷评分", "HRD检测（卵巢癌）", "LOH TAI LST综合评分"],
        "description": "肿瘤基因组层面HRD定量检测，通过LOH、TAI、LST三项指标计算HRDscore（阈值42），单独或联合HRR突变判断卵巢/乳腺肿瘤对PARP抑制剂、铂类化疗敏感性，指导维持治疗方案",
        "key_fields": ["LOH分值", "TAI分值", "LST分值", "HRD总分", "HRD阳性判定标准", "PARP抑制剂用药提示", "铂类获益提示"],
        "priority": "high",
    },
    "UGT1A1_IRINOTECAN": {
        "aliases": ["伊立替康用药评估", "UGT1A1基因检测", "伊立替康毒性筛查", "UGT1A1*6 *28位点"],
        "description": "结直肠癌、胃癌、肺癌伊立替康化疗安全用药标志物，检测UGT1A1 rs4148323(*6)、rs3064744(*28)关键多态位点，评估SN-38代谢能力，预测迟发性腹泻、中性粒细胞减少等重度毒副作用，指导剂量调整",
        "key_fields": ["UGT1A1 rs位点基因型", "TA重复数", "酶活性判断", "毒副作用风险等级", "化疗剂量建议", "PharmGKB证据等级"],
        "priority": "high",
    }
}

# ============================================================
# 二、模型与文本提取参数
# ============================================================
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv('OPENAI_API_KEY')
BASE_URL = os.getenv('OPENAI_BASE_URL')
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
  "指标名": {"is_report_type": "yes 或 no", "status": "细化状态", "evidence": "引用原文关键片段，便于人工复核"},
  "指标名": {"is_report_type": "yes 或 no", "status": "细化状态", "evidence": "引用原文关键片段，便于人工复核"}
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
  "指标名": {
    "summary": "结论性解读，约150字以内",
    "key_values": {"关键字段名": "从原文提取的对应数值"},
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
    # 保存为 JSON 文件
    os.makedirs('json', exist_ok=True)
    name = filename.replace('.pdf', '')
    with open(f'json/{name}.json', 'w', encoding='utf-8') as f:
        json.dump(step2_result, f, ensure_ascii=False, indent=4)
    print(f"JSON已保存到{name}.json")

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
    pdf_file = 'BRCA1+BRCA2基因检测（卵巢癌）.pdf'
    report_text, structured_result = generate_report(pdf_file)
    print(report_text)
    print(structured_result)
