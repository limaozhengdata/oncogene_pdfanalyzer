# -*- coding:utf-8 -*-
"""
@coding_address:Jinxiu
@coding_edition:python3.6
@author:李茂正
@date:2026/7/24 14:49
"""
import os
import re
import json
import time
import requests
import paramiko
import pandas as pd
from io import BytesIO
from openai import OpenAI

import ObsUploadPDF
from tool.mysql import Mysql
from ObsUploadPDF import upload_pdf


def get_replacer():
    p_dct = {
        'Gly': 'G', 'Ala': 'A', 'Val': 'V', 'Leu': 'L', 'Ile': 'I', 'Pro': 'P',
        'Phe': 'F', 'Tyr': 'Y', 'Trp': 'W', 'Ser': 'S', 'Thr': 'T', 'Cys': 'C',
        'Met': 'M', 'Asn': 'N', 'Gln': 'Q', 'Asp': 'D', 'Glu': 'E', 'Lys': 'K',
        'Arg': 'R', 'His': 'H', 'Ter': '*'
    }

    return replacer_factory(p_dct), re.compile('|'.join(p_dct.keys()))


def replacer_factory(p_dct):
    return lambda match: p_dct[match.group(0)]


def mut_regular(mut):
    """
    使用大模型规范化基因突变信息
    """
    start_time = time.time()
    client = OpenAI(
        # api_key="sk-c9033ccf97e74cf99d58d4f04b2d42c1",
        # base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-ws-H.ERYEELI.YkPJ.MEUCID42GiXlekxWWZl6lyOnoZHtk6dl9bYQJndTHenjl8m1AiEA_jpiLZYUyUKC41jGgmkzj8VjjIywcbOSP3wpurwFZwM",
        base_url="https://llm-ajfzamzw7t6tpadu.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    SYSTEM_PROMPT = """你是一个专业的基因突变标准化助手。你的任务是将用户输入的基因突变信息提取并转换为标准格式。
    请严格遵循以下标准化规则进行转换，并**排除非基因名（如染色体区域、染色体号、位置区间等）**的项目。
    请遵循以下规则：
    1. SNV突变格式：基因名-p.具体突变 (例如：KRAS:p.G12C)
    2. CNV变异格式：基因名-变异类型(gain/loss) (例如：ERBB2:gain)
    3. 基因融合格式：基因A-基因B (例如：BCR::ABL1)

    示例转换：
    - kras G12C → KRAS:p.G12C:NM号:SOM
    - ERBB2扩增/缺失 → ERBB2:gain/loss
    - ALK融合 → .::ALK
    - RET融合 → .::RET
    - BRAF-ALK → BRAF::ALK

    请以JSON格式返回结果，格式如下：
    {
      "snv": ["基因名:p.具体突变", ...],
      "fus": ["基因A::基因B", ".::基因A", ...],
      "cnv": ["基因名:变异类型", ...]
    }
    只返回JSON格式的结果，不需要其他解释。"""

    USER_PROMPT = f"""请将以下文本中患者检测到的基因变异转换为标准格式：
    mut: {mut}"""

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": USER_PROMPT}],
                temperature=0.1,
                stream=False
            )

            result = json.loads(response.choices[0].message.content)
            # 确保所有键存在
            return_data = {key: result.get(key, []) for key in ["snv", "fus", "cnv"]}
            # 检查是否所有字段都为空
            if any(return_data.values()):  # 如果有任何非空数据
                print(f"第{attempt + 1}次调用成功")
                return return_data
            else:
                print(f"第{attempt + 1}次调用返回空数据，重试中...")

        except Exception as e:
            print(f"第{attempt + 1}次调用失败: {e}")
            # return {"snv": [], "fus": [], "cnv": []}
        finally:
            print(f'第{attempt + 1}次耗时: {round(time.time() - start_time, 2)}秒')
    print("所有重试均失败，返回空数据")
    return {"snv": [], "fus": [], "cnv": []}


def create_remote_folder_and_json(json_data, folder):
    # 目标服务器信息
    host = "192.168.135.10"
    port = 22  # SSH 端口
    username = "meddb"
    password = "HS#&79074!@"  # 或使用密钥认证
    remote_path = f"/home/meddb/machao_tf/meta_sample_path/test_sample/{folder}"  # 远程路径
    remote_json_path = f"{remote_path}/{folder}_all_mut.json"
    try:
        # 1. 创建 SSH 连接
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, username, password, timeout=10)
        print(f'SSH连接成功: {host}')

        # 2. 创建远程文件夹
        stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {remote_path}")
        error = stderr.read().decode('utf-8').strip()
        if error:
            raise Exception(f"创建目录失败: {error}")

        # 3. 设置权限
        stdin, stdout, stderr = ssh.exec_command(f"chmod 777 {remote_path}")
        print('创建目录并设置权限成功')

        # 3. 上传 JSON 文件
        sftp = ssh.open_sftp()
        with BytesIO(json.dumps(json_data, ensure_ascii=False).encode('utf-8')) as fl:
            sftp.putfo(fl, remote_json_path)
        print(f'JSON文件上传成功: {remote_json_path}')
        print('所有操作完成！')
    except Exception as e:
        print(f"操作失败: {str(e)}")
        raise
    finally:
        # 关闭连接
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()
            print('SSH连接已关闭')


def execute_remote_commands(folder, cancer):
    host = "192.168.135.10"
    port = 22
    username = "worker"
    password = "qcerer"  # 强烈建议改用SSH密钥认证
    remote_dir = f"/home/meddb/machao_tf/meta_sample_path/test_sample/{folder}"
    sample_prefix = folder
    try:
        # 1. 创建SSH连接
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, username, password, timeout=10)
        print('worker用户连接成功！')
        # 2. 构建命令
        # base_path = "/fastzone/worker/knowledge_database/smartonco/script/soip_v2"
        base_path = "/fastzone/worker/knowledge_database/smartonco/testscript/machao_soip2"
        venv_python = "/fastzone/worker/knowledge_database/ctdna_venv/bin/python"
        # 构建完整命令（用 && 确保顺序执行）
        commands = [
            f"cd {base_path}/sokg/AiReport/",
            f"{venv_python} PDF2Mut.py {folder} {cancer}",
            f"{venv_python} {base_path}/ctdna.py  -c chem -kb smartonco_4 -p  {remote_dir}/{sample_prefix}-CSF -n wes"
        ]
        print('正在运行ctdna程序，请稍后……')
        # 3. 合并命令（用&&连接，确保顺序执行）
        full_command = " && ".join(commands)

        # 4. 执行命令（添加环境变量和正确的shell）
        stdin, stdout, stderr = ssh.exec_command(f"bash -lc '{full_command}'", get_pty=True)

        # 5. 等待命令执行完成
        exit_status = stdout.channel.recv_exit_status()
        output = "".join(stdout.readlines()).strip()
        error_output = "".join(stderr.readlines()).strip()

        # 6. 检查执行结果
        if exit_status == 0:
            print("ctdna脚本执行成功！")
            if output:
                print(f"输出: {output}")
        else:
            print(f"执行失败，状态码: {exit_status}")
            if error_output:
                print(f"错误信息: {error_output}")
            elif output:
                print(f"输出信息: {output}")
    except Exception as e:
        print(f"执行远程命令失败: {str(e)}")
        raise
    finally:
        if ssh:
            ssh.close()
            print('SSH连接已关闭')


def extract_pdf(file_path, filename, MAX_LEN):
    with open(file_path, 'rb') as file:
        files = {'file': (filename, file, 'application/pdf')}
        response = requests.post('http://192.168.135.233:9999/upload', files=files, timeout=3000)
        content = response.json()
        textdata = content['textdata']
        textdata = re.sub(r'<[^>]+>', '', textdata)
        print("原始 textdata 长度:", len(textdata))
        if len(textdata) >= MAX_LEN:
            textdata = textdata[:MAX_LEN]
        return textdata


def add_nm_annotation(mutations, mysql):
    """
    为变异添加NM注释
    """
    new_row = []
    for item in mutations:
        gene = item.split(':')[0]
        sql = f'SELECT symbol, new_accession FROM gene2nm_update WHERE symbol="{gene}"'
        df = pd.DataFrame(mysql.fetch_all(sql))
        if not df.empty:
            df_dict = dict(zip(df['symbol'], df['new_accession']))
            NM = df_dict.get(gene)
            if NM:
                new_item = f'{item}:{NM}:SOM'
                new_row.append(new_item)
    return new_row


def format_show(sampleid, result, mysql):
    dct = {}
    db = 'smartoncointerpretation_v2'
    if len(result['cnv']) > 0:
        cnv_sql = f'SELECT genesymbol,cnv_result AS variation,drug_ch,relateddisease,drugefficacy FROM {db}.`cnv_drug` where sampleid="{sampleid}" '
        cnv_df = mysql.fetch_all(cnv_sql)
        dct['cnv_res'] = cnv_df
    if len(result['snv']) > 0:
        snv_sql = f'SELECT genesymbol,CASE WHEN phgvs != "NA" THEN phgvs WHEN chgvs != "NA" THEN chgvs ELSE "/" END AS variation,relateddisease,drug_ch,drugefficacy FROM {db}.`snv_drug` where sampleid="{sampleid}" '
        snv_df = mysql.fetch_all(snv_sql)
        dct['snv_res'] = snv_df
    if len(result['fus']) > 0:
        fus_sql = f'SELECT genesymbol,CONCAT(fivetailgenesymbol, "-", threetailgenesymbol) AS variation,relateddisease,drug_ch,drugefficacy FROM {db}.`fus_drug` where sampleid="{sampleid}" '
        fus_df = mysql.fetch_all(fus_sql)
        dct['fus_res'] = fus_df
    print(dct)
    return dct


def process_pdf_file(filename, sampleid, cancer):
    UPLOAD_DIR = "/home/node9/xg/pdf_parse/uploads"
    DB_CONF_FILE = 'conf/db.smartonco_4.conf'
    mysql = Mysql(db_conf_file=DB_CONF_FILE)
    MAX_LEN = 57344
    try:
        start_time = time.time()
        # 1. 构建文件路径
        file_path = os.path.join(UPLOAD_DIR, filename)
        if not os.path.exists(file_path):
            upload_pdf(filename)
        print(f"处理文件: {file_path}")
        # 2.解析pdf文件
        textdata = extract_pdf(file_path, filename, MAX_LEN)
        # 3.deepseek识别变异信息
        result = mut_regular(textdata)
        print('deepseek 识别出:', result)
        # if not result or all(len(v) == 0 for v in result.values()):
        #     raise ValueError(f"DeepSeek未识别到任何变异信息！样本: {sampleid}, 文件: {filename}")
        # 4.标准化变异格式
        replacer, pattern = get_replacer()
        result['snv'] = [
            f"{gene}:{pattern.sub(replacer, mutation)}" if ':' in item else item
            for item in result['snv']
            for gene, mutation in [item.split(':', 1)]
        ]
        print('变异标准化后:', result)
        # 5.添加变异NM注释
        result['snv'] = add_nm_annotation(result.get('snv', []), mysql)
        print("NM标准化后：", result)
        # 6.创建远程json文件
        create_remote_folder_and_json(result, sampleid)
        # 7.执行ctdna程序
        execute_remote_commands(sampleid, cancer)
        # 8.封装数据
        dct = format_show(sampleid, result, mysql)
        print('所有流程全部运行完成！')
        print(f'程序总耗时: {round(time.time() - start_time, 2)}秒')
        return dct

    except FileNotFoundError:
        print(f"错误：文件 {filename} 不存在于 {UPLOAD_DIR}")
        raise
    except requests.exceptions.RequestException as e:
        print(f"网络请求失败: {e}")
        raise
    except Exception as e:
        print(f"处理失败: {str(e)}")
        raise


if __name__ == '__main__':
    filename = '6012340402-陈金根-2026-08-04 16_45_03 (3).pdf'  #
    sampleid = 'S2600015708'
    cancer = "肠癌"
    # 处理PDF
    result = process_pdf_file(filename, sampleid, cancer)
    print(f"最终结果: {result}")
