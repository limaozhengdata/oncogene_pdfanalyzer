# -*- coding: utf-8 -*-
# @Author: Eassi Chan
# @Date:   2018-05-21 14:19:15
# @Last Modified by:   Eassi Chan
# @Last Modified time: 2018-07-31 17:35:30

"""
封装数据库增删查改方法发一个类
"""

import os
import re
import yaml
from configparser import ConfigParser
import pymysql
import pandas as pd
# from tool.configlog import config_log
# from tool.utils import get_execute_time


class Mysql(object):
    """ Mysql database accessor.
    """

    def __init__(self, db_conf_file):
        self.err_handler = MysqlErrorHandler()
        self.config_file = db_conf_file

        cfg = ConfigParser()
        config_file_path = os.path.join(os.path.dirname(__file__), '..', db_conf_file)
        cfg.read(filenames=config_file_path, encoding='utf-8')
        host = cfg.get('database', 'host')
        port = cfg.get('database', 'port')
        user = cfg.get('database', 'user')
        password = cfg.get('database', 'password')
        db = cfg.get('database', 'db')
        charset = cfg.get('database', 'charset')
        del cfg

        self.conn = pymysql.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            db=db,
            charset=charset,
            cursorclass=pymysql.cursors.DictCursor
        )
        self.cursor = self.conn.cursor()

        # self.logger = config_log(os.path.basename(__file__), warn=True)
        return

    def connect(self):
        """ 根据配置文件的连接参数连接数据库. """
        self._configure()
        try:
            self.conn = pymysql.connect(**self.config)
        except pymysql.err.DatabaseError as e:
            self.err_handler.handle_connecting(e)
        else:
            self.is_connected = True
        # server = SSHTunnelForwarder(('bdmswc.smartquerier.com', 3322),  # 跳板机ip及端口
        #                             ssh_username='sfyang',  # 跳板机账号
        #                             ssh_password='yangsf',  # 跳板机密码
        #                             remote_bind_address=(host, int(port)))  # 目标数据库服务器ip、端口
        #
        # server.start()  # 启动连接管道
        # self.conn = pymysql.connect(
        #     host='127.0.0.1',  # 此处必须是是127.0.0.1
        #     port=server.local_bind_port,  # api固定写法
        #     user=user,  # 目标数据库账号
        #     passwd=password,  # 目标数据库密码
        #     charset=charset,
        #     use_unicode=True,
        #     db=db,
        #     cursorclass=pymysql.cursors.DictCursor)
        # self.cursor = self.conn.cursor()
        # return

    def _configure(self):
        """ 获取数据库连接参数. """
        self._read_config_file()
        self._validate_config()

    def _read_config_file(self):
        """ 读取yaml格式的数据库配置文件. """
        err_msg = "文件‘%s’不存在！" % os.path.abspath(self.config_file)
        assert os.path.exists(self.config_file), err_msg

        fo = open(self.config_file, 'r', encoding='utf-8')
        # self.config = yaml.load(fo, Loader=yaml.FullLoader)
        self.config = yaml.load(fo)
        fo.close()

    def _validate_config(self):
        """ 验证数据库连接参数的正确性或为其设置默认值. """
        # 必要参数：
        host = self.config.get('host', None)
        user = self.config.get('user', None)
        password = self.config.get('password', None)
        db = self.config.get('db', None)

        err_msg = "数据库连接配置文件中需要指定服务器地址、用户名和密码"
        assert (host and user and password and db), err_msg

        # 可选参数（如果配置文件没有列出则为其设置默认值）：
        self.config['port'] = self.config.get('port', 3306)
        self.config['charset'] = self.config.get('charset', 'utf8')
        self.config['use_unicode'] = self.config.get('use_unicode', True)
        self.config['sql_mode'] = self.config.get('sql_mode',
                                                  'STRICT_TRANS_TABLES')

        # getattr方法的‘name’位置参数必须是字符串，因此‘cur_cls’的默认值不能为None
        cur_cls = self.config.get('cursorclass', '')
        default_cur_cls = pymysql.cursors.DictCursor
        self.config['cursorclass'] = getattr(pymysql.cursors, cur_cls,
                                             default_cur_cls)

    def reconnect(self):
        """ 重新连接数据库. """
        self.conn.ping()
        self.is_connected = True

    def open_cursor(self):
        """ 获取操作数据库的游标. """
        return self.conn.cursor()

    # @get_execute_time(is_sql=True)
    def execute(self, sql, args=None):
        # assert self.is_connected, "操作数据库前请先连接数据库，谢谢！"
        try:
            rcount = self.cursor.execute(query=sql, args=args)  # rcount: row count

        except pymysql.err.DatabaseError as e:
            self.rollback()
            self.err_handler.handle_executing(e, cursor=self.cursor, err_sql=sql,
                                              err_args=args)
        else:
            return rcount

    def execute_many(self, sql, args=None, deadlock_count=0):
        if args is not None and not isinstance(args, list):
            raise TypeError("对数据库进行批量插入时，请使用列表结构存放要插入的数据！")
        try:
            rcounts = self.cursor.executemany(query=sql, args=args)
        except pymysql.err.DatabaseError as e:
            self.rollback()
            self.err_handler.handle_executing(e, cursor=self.cursor, err_sql=sql, err_args=args)
        else:
            return rcounts

    def updatemany(self, sql, args=None):
        row_affected = self.execute_many(sql, args=args)
        return row_affected

    def select_db(self, db_name):
        """ 选择要操作的数据库. """
        sql = "use %s" % db_name
        self.execute(sql=sql)

    def fetch_one(self, sql, args=None):
        self.execute(sql=sql, args=args)
        qresult = self.cursor.fetchone()
        return qresult

    def fetch_all(self, sql, args=None):
        self.execute(sql=sql, args=args)
        qresults = self.cursor.fetchall()
        return qresults

    def insert_one(self, sql, args=None):
        row_affected = self.execute(sql=sql, args=args)
        return row_affected

    def insert_many(self, sql, args=None):
        row_affected = self.execute_many(sql, args=args)
        return row_affected

    def update(self, sql, args=None):
        row_affected = self.execute(sql=sql, args=args)
        return row_affected

    def delete(self, sql, args=None):
        row_affected = self.execute(sql=sql, args=args)
        return row_affected

    @property
    def rowcount(self):
        '''影响的行数'''
        return self.cursor.rowcount

    def commit(self):
        self.conn.commit()
        return

    def rollback(self):
        self.conn.rollback()
        return

    def __del__(self):
        if getattr(self, 'cursor', None) and getattr(self, 'conn', None):
            self.cursor.close()
            self.conn.close()
        return

    def close(self):
        self.__del__()

    def sql2df(self, sql, args=None):
        qresults = self.fetch_all(sql, args=args)
        if len(qresults) > 0:
            idf = pd.DataFrame(data=qresults).fillna('NA')
        else:  # 如果没有查询到当前样本的相关记录，就返回一个空的DataFrame
            # print('没有查询到有关SampleId为%s的数据库记录！' % sampleid)
            idf = pd.DataFrame()
        return idf

    def insert_datas(self, sql, sampleid, datas):
        # delete last output:
        self.execute(
            sql=sql['delete_last'],
            args=dict(sampleid=sampleid)
        )
        self.commit()
        # insert current output:
        self.insert_many(
            sql=sql['insert_current'],
            args=datas
        )
        self.commit()
        return

    def get_df2tb_sql(self, df, table, db_name=None):
        if not db_name:
            db_name = self.conn.db.decode()
        try:
            df.drop(['recordupdatetime'], axis=1, inplace=True)
        except:
            pass
        df_columns_list = list(df.columns)
        # 查询某个表字段
        sql = f"""select COLUMN_NAME from information_schema.COLUMNS where table_schema = "{db_name}" and table_name = '{table}'"""
        qresults = self.fetch_all(sql)
        table_columns_list = [qresult['COLUMN_NAME'] for qresult in qresults]
        columns_list = set(df_columns_list).intersection(set(table_columns_list))  # 取df字段和数据库字段交集
        columns_list.discard('id')
        fields = ','.join([f'`{column}`' for column in columns_list])
        values = ','.join([f'%({column})s' for column in columns_list])
        sql = f"insert into {table} ({fields}) values ({values})"

        return sql, columns_list

    def insert_sql(self, df, table, db_name=None):
        if not db_name:
            db_name = self.conn.db.decode()
        try:
            df.drop(['recordupdatetime'], axis=1, inplace=True)
        except:
            pass
        df_columns_list = list(df.columns)
        # 查询某个表字段
        sql = f"""select COLUMN_NAME from information_schema.COLUMNS where table_schema = "{db_name}" and table_name = '{table}'"""
        qresults = self.fetch_all(sql)
        table_columns_list = [qresult['COLUMN_NAME'] for qresult in qresults]
        columns_list = set(df_columns_list).intersection(set(table_columns_list))  # 取df字段和数据库字段交集
        columns_list.discard('id')
        fields = ','.join([f'`{column}`' for column in columns_list])
        values = ','.join([f'%({column})s' for column in columns_list])
        sql = f"insert into `{table}` ({fields}) values ({values})"
        return sql

    def delete_last_output(self, sampleid, tb_name):
        delete_sql = f"delete from {tb_name} where sampleid=%(sampleid)s"
        self.execute(
            sql=delete_sql,
            args=dict(sampleid=sampleid)
        )

    def save_df2db_by_sid(self, sampleid, odf, tb_name, db_name=None):
        self.delete_last_output(sampleid, tb_name)
        # self.commit()
        # insert current output:
        if not odf.empty:
            insertsql, col_list = self.get_df2tb_sql(odf, tb_name, db_name)
            row_affected = self.insert_many(
                sql=insertsql,
                args=odf[col_list].to_dict(orient='records')
            )
        else:
            row_affected = 0
        # self.commit()
        return row_affected


class MysqlErrorHandler:
    """ 处理操作Mysql时抛出的各种常见的异常. """

    def __init__(self):
        self.err_type = (
            pymysql.err.DataError,
            pymysql.err.OperationalError,
            pymysql.err.IntegrityError,
            pymysql.err.InternalError,
            pymysql.err.ProgrammingError,
            pymysql.err.NotSupportedError,
        )

        self.err_map = {
            # <数据库错误码>: <中文的出错提示信息>
            # pymysql.err.OperationalError，一般是连接数据库出错时抛出:
            1045: "连接数据库用的账号和密码有误！",
            2013: "与数据库服务器的连接已中断，可能是数据库服务器关机，也可能是网络断开.",
            # pymysql.err.ProgrammingError:
            1064: "SQL语句有语法错误！",
            1146: "要查询的数据表并不存在！",
            # pymysql.err.DataError:
            1406: "不能插入超过字段长度限制的数据！",
            # pymysql.err.InternalError:
            1046: "并没有选择要操作的数据库！",
            1054: "要插入的数据中包含数据表未定义的字段！",
        }

    def handle_connecting(self, e):
        """ 处理连接数据库时出现的错误或异常. """
        if not isinstance(e, self.err_type):
            raise e
        err_no = e.args[0]
        err_tip = e.args[1]
        err_msg = self.err_map.get(err_no, None)
        if err_msg:
            e.args = tuple([err_no, err_tip, err_msg])
        raise e

    def handle_executing(self, e, cursor, err_sql, err_args):
        """ 处理执行SQL语句时出现的错误或异常. """
        if not isinstance(e, self.err_type):
            raise e
        err_no = e.args[0]
        err_tip = e.args[1]
        err_msg = self.err_map.get(err_no, None)
        if err_msg:
            err_sql = cursor.mogrify(query=err_sql, args=err_args)
            err_sql = self.format_sql(err_sql)
            e.args = tuple([err_no, err_tip, err_msg, err_sql])
        raise e

    @staticmethod
    def format_sql(sql):
        """ 格式化SQL语句. """
        sql = re.sub(r'\s+', ' ', sql).strip()
        sql = "出错的SQL语句是：%s" % sql
        return sql
