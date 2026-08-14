# -*- coding:utf-8 -*-
"""
@coding_address: Kangan
@coding_edition: Python 3.6.8
@author: 李茂正
@date: 2026/8/11 10:36
"""
# !/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
独立 Demo：登录拿 token -> 申请 OBS 临时 AK/SK -> 从 obs://bdms/yananai/... 下载 PDF

1) POST {API_BASE}/backend/user/login
2) POST {API_BASE}/cloud/huawei/template/accessKey (Header: huisuan-token)
3) 用临时 AK/SK + securitytoken 调用 obsclient.getObject 下载

依赖：requests, obsclient (esdk-obs-python)
安装：pip install requests esdk-obs-python
"""

import os
import requests
from obs import ObsClient

# ========== 发给别人时只需改这里 ==========
# 后端地址（含 context-path /api），例如本地：http://localhost:9082/api
API_BASE = "http://192.168.135.250:30819/api"

# 运营后台账号密码（用于登录拿 token）
USER_NAME = "limaozheng"
PASSWORD = "12345678"

# OBS 对象 Key（不要带 obs://bdms/ 前缀）
# 例如：yananai/2026-08/xxx.pdf
OBS_OBJECT_KEY = "yananai/2026-08/6012340402-陈金根-2026-08-04 16_45_03 (3).pdf"

# 本地下载保存路径（目录不存在会自动创建）
# LOCAL_SAVE_PATH = r"C:\Users\hxy\Downloads\obs-download-demo.pdf"
SAVE_PATH = f'/home/node9/xg/pdf_parse/uploads'
# ========================================

LOGIN_PATH = "/backend/user/login"
ACCESS_KEY_PATH = "/cloud/huawei/template/accessKey"


def trim_trailing_slash(base):
    """去掉末尾的斜杠"""
    if not base:
        return ""
    return base.rstrip("/")


def normalize_object_key(key):
    """
    兼容误填 obs://bdms/xxx 或 /xxx 的情况
    obs://bdms/yananai/xxx.pdf -> yananai/xxx.pdf
    """
    value = key.strip()
    if value.startswith("obs://"):
        # 去掉 obs://bucket/ 前缀
        idx = value.find("/", len("obs://"))
        if idx >= 0 and idx + 1 < len(value):
            value = value[idx + 1:]
    while value.startswith("/"):
        value = value[1:]
    return value


def login_and_get_token():
    """
    POST /backend/user/login
    请求：{"userName":"...","password":"..."}
    响应：{"status":0,"data":"token字符串"}
    """
    url = trim_trailing_slash(API_BASE) + LOGIN_PATH
    req_body = {
        "userName": USER_NAME,
        "password": PASSWORD
    }

    response = requests.post(url, json=req_body, timeout=30)
    print(f"login HTTP status = {response.status_code}")

    if response.status_code < 200 or response.status_code >= 300:
        raise Exception(f"登录 HTTP 失败: {response.status_code} body={response.text}")

    body = response.json()
    if body is None:
        raise Exception("登录响应为空")

    status = body.get("status")
    if status != 0:
        raise Exception(f"登录业务失败: status={status}, message={body.get('message')}")

    token = body.get("data")
    if not token:
        raise Exception("登录成功但 data(token) 为空")

    return token


def fetch_temp_access_key(huisuan_token):
    """
    POST /cloud/huawei/template/accessKey
    Header：huisuan-token
    响应 data：access / secret / securitytoken / bucket / endpoint
    """
    url = trim_trailing_slash(API_BASE) + ACCESS_KEY_PATH
    headers = {
        "Content-Type": "application/json",
        "huisuan-token": huisuan_token
    }

    response = requests.post(url, headers=headers, timeout=30)
    print(f"accessKey HTTP status = {response.status_code}")
    print(f"accessKey body = {response.text}")

    if response.status_code < 200 or response.status_code >= 300:
        raise Exception(f"申请临时密钥 HTTP 失败: {response.status_code} body={response.text}")

    body = response.json()
    if body is None:
        raise Exception("申请临时密钥响应为空")

    status = body.get("status")
    if status != 0:
        raise Exception(f"申请临时密钥业务失败: status={status}, message={body.get('message')}")

    data = body.get("data")
    if not data:
        raise Exception("申请临时密钥 data 为空")

    return data


def upload_pdf(OBS_OBJECT_KEY):
    if "请替换" in USER_NAME or "请替换" in PASSWORD:
        raise Exception("请先配置有效的 USER_NAME / PASSWORD")

    if not OBS_OBJECT_KEY or not OBS_OBJECT_KEY.strip():
        raise Exception("请先配置 OBS_OBJECT_KEY，例如 yananai/2026-08/xxx.pdf")

    object_key = normalize_object_key(OBS_OBJECT_KEY)
    filename = OBS_OBJECT_KEY.split('/')[-1]
    # 1. 登录拿 huisuan-token
    token = login_and_get_token()
    print("登录成功，已拿到 token")

    # 2. 调接口拿临时密钥
    temp = fetch_temp_access_key(token)
    access_key = temp.get("access")
    secret_key = temp.get("secret")
    security_token = temp.get("securitytoken")
    bucket = temp.get("bucket")
    endpoint = temp.get("endpoint")

    print(f"临时密钥申请成功, bucket={bucket}, endpoint={endpoint}")
    print(f"准备下载: obs://{bucket}/{object_key}")

    # 3. 临时 AK/SK 必须带 securitytoken 初始化客户端后再下载
    LOCAL_SAVE_PATH = os.path.join(SAVE_PATH, filename)
    save_file = os.path.join(LOCAL_SAVE_PATH) if os.path.isabs(LOCAL_SAVE_PATH) else os.path.abspath(LOCAL_SAVE_PATH)
    parent_dir = os.path.dirname(save_file)
    # parent_dir = '/home/node9/xg/pdf_parse/uploads'
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    # 创建 OBS 客户端
    obs_client = ObsClient(
        access_key_id=access_key,
        secret_access_key=secret_key,
        security_token=security_token,
        server=endpoint
    )

    try:
        # 下载对象
        resp = obs_client.getObject(bucket, object_key, downloadPath=save_file)
        if resp.status < 300:
            print("下载成功")
            print(f"objectKey = {object_key}")
            print(f"本地路径 = {save_file}")
            # 获取文件大小
            if os.path.exists(save_file):
                file_size = os.path.getsize(save_file)
                print(f"文件大小 = {file_size} bytes")
        else:
            error_msg = resp.errorCode if hasattr(resp, 'errorCode') else "未知错误"
            raise Exception(f"下载失败: {error_msg}")
    finally:
        obs_client.close()


if __name__ == "__main__":
    upload_pdf('yananai/2026-08/6012340402-陈金根-2026-08-04 16_45_03 (3).pdf')
