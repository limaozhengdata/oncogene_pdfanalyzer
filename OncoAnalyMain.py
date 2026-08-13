# -*- coding:utf-8 -*-
"""
@coding_address: Kangan
@coding_edition: Python 3.6.8
@author: 李茂正
@date: 2026/8/13 13:52
"""

from OncoReport_Generator import generate_report
from OncoGene_PDFAnalyzer import process_pdf_file

if __name__ == '__main__':
    filename = '未命名1_加水印.pdf'  #
    sampleid = 'S2600015708'  # S2600015708
    cancer = "肠癌"
    # 处理PDF
    result = process_pdf_file(filename, sampleid, cancer)
    print(f"最终结果: {result}")
    report_text, structured_result = generate_report(filename)
    print('正在解读……')
    print(report_text)
    print(structured_result)
