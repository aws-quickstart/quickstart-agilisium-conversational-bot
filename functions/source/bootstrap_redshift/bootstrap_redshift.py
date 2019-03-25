import psycopg2
import base64
import csv
from botocore.vendored import requests
import boto3
from boto3.session import Session
import json
import logging
import os
import dateutil.parser

SUCCESS = "SUCCESS"
FAILED = "FAILED"

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
session = Session()
region_name = session.region_name
s3_service = boto3.client('s3')
table_names = ['customers', 'employees', 'offices', 'orderdetails', 'orders', 'payments', 'productlines', 'products']

if 'LOG_LEVEL' in os.environ:
    if os.environ['LOG_LEVEL'] == 'DEBUG':
        logger.setLevel(logging.DEBUG)
    if os.environ['LOG_LEVEL'] == 'INFO':
        logger.setLevel(logging.INFO)
    if os.environ['LOG_LEVEL'] == 'WARNING':
        logger.setLevel(logging.WARNING)


def lambda_handler(event, context):
    response_data = {}
    try:
        logger.debug("Received event: %s", json.dumps(event))
        logger.debug("Event type: %s", event['RequestType'])
        logger.info(event)
        if event['RequestType'] in ['Create','Update']:
            is_processed = True
            if not valid_properties(event, context,
                                    ['DatabaseName', 'DatabasePort', 'MasterUsername', 'MasterUserPassword',
                                     'RedshiftCluster']):
                raise RuntimeError("Missing one of the custom lambda properties..")
            is_processed = insert_records(event)
            if not is_processed:
                raise RuntimeError("Failed to create or load  data")
            if is_processed:
                response_data['Message'] = "dataloading creation successful"
                send(event, context, SUCCESS, response_data, None)
            else:
                response_data['Message'] = "dataloading creation failed"
                send(event, context, FAILED, response_data, None)
        elif event['RequestType'] == 'Delete':
            try:
                response_data['Message'] = 'Delete redshift cluster'
                send(event, context, SUCCESS, response_data, event['PhysicalResourceId'])
            except Exception as ex:
                is_processed = False
                logger.info("Exception during get database details, failed to delete..." + str(ex))
                send(event, context, FAILED, response_data, event['PhysicalResourceId'])
                return
    except Exception as e:
        response_data['Message'] = "Unexpected error: " + str(type(e)) + ": " + str(e.args)
        print(response_data['Message'])
        send(event, context, FAILED, response_data, None)

    return


# Decode encrypted values
def decoded_value(kms_client, encrypted_value):
    decoded_value = ''
    try:
        binary_data = base64.b64decode(encrypted_value)
        meta = kms_client.decrypt(CiphertextBlob=binary_data)
        plain_text = meta[u'Plaintext']
        decoded_value = plain_text.decode()
    except Exception as ex:
        print('exception while trying to decrypt : ' + str(ex))

    return decoded_value


def get_redshift_connection(host, user, password, port, db_name):
    # getting database connection

    try:
        db_connection = psycopg2.connect(dbname=db_name, host=host, port=port, user=user,
                                         password=password)
        return db_connection
    except Exception as ex:
        print('exception while trying to connect redshift : ' + str(ex))


def valid_properties(event, context, mandatory_property_names):
    """Validate the event structure"""
    missing_property_names = []
    response_data = {}
    if not 'ResourceProperties' in event:
        response_data['Message'] = "Malformed CloudFormation request, missing ResourceProperties"
        send(event, context, FAILED, response_data, None)
        return False
    for mandatory_property_name in mandatory_property_names:
        if not mandatory_property_name in event['ResourceProperties']:
            missing_property_names.append(mandatory_property_name)
    if len(missing_property_names) > 0:
        response_data['Message'] = "Missing one or more required properties: {0}".format(missing_property_names)
        send(event, context, FAILED, response_data, None)
        return False
    return True


def send(event, context, response_status, response_data, physical_resource_id):
    responseUrl = event['ResponseURL']
    logger.debug("CF Response URL: " + responseUrl)
    response_body = {}
    response_body['Status'] = response_status
    if response_status == FAILED:
        response_body['Reason'] = response_data['Message']
    else:
        response_body['Reason'] = "completed"
    response_body['StackId'] = event['StackId']
    response_body['PhysicalResourceId'] = 'RedshiftDataLoaderStaticPhysicalResourceId'#physical_resource_id or context.log_stream_name
    response_body['RequestId'] = event['RequestId']
    response_body['LogicalResourceId'] = event['LogicalResourceId']
    response_body['Data'] = response_data
    json_response_body = json.dumps(response_body)
    logger.info("CF Response Body: %s", json.dumps(json_response_body))
    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }

    try:
        response = requests.put(responseUrl, data=json_response_body, headers=headers)
        logger.info("CF Status code: %s ", response.reason)
    except Exception as e:
        logger.error("send(..) failed executing requests.put(..): %s", str(e))


# Delete Tables if Already Exists
def delete_tables_ifExist(connection):
    is_processed = True
    try:
        cur = connection.cursor()
        drop_query = "DROP TABLE IF EXISTS customers,employees,offices,orderdetails,orders,payments,productlines,products"
        cur.execute(drop_query)
        connection.commit()
        cur.close()
    except Exception as ex:
        is_processed = False
        logger.error("Exception While Trying to Drop existing tables"+str(ex))
    return is_processed


# Insert records
def insert_records(event):
    is_processed = True
    try:
        db_name = event['ResourceProperties']['DatabaseName']
        end_point = event['ResourceProperties']['RedshiftCluster']
        db_port = event['ResourceProperties']['DatabasePort']
        db_username = event['ResourceProperties']['MasterUsername']
        db_password = event['ResourceProperties']['MasterUserPassword']
        connection = get_redshift_connection(end_point, db_username, db_password, db_port, db_name)
        is_processed = delete_tables_ifExist(connection)
        if is_processed:
            is_processed = create_tables(connection)
            bucket_name = event['ResourceProperties']['BucketName']
            path = event['ResourceProperties']['Path']
            # copy data
            if is_processed:
                cursor = connection.cursor()
                for table in table_names:
                    table_data = s3_service.get_object(Bucket=bucket_name, Key=path + '/' + table + '.csv')
                    text_object_data = table_data['Body'].read().decode('utf-8','ignore')
                    csv_data = csv.reader(text_object_data.split("\n"), delimiter=',')
                    logger.info('started processing table :'+str(table))
                    if table == 'customers':
                        save_customers_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'employees':
                        save_employees_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'offices':
                        save_offices_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'orderdetails':
                        save_orderdetails_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'orders':
                        save_orders_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'payments':
                        save_payments_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'productlines':
                        save_productlines_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                    elif table == 'products':
                        save_products_data(csv_data, cursor, connection)
                        logger.info('completed processing table :' + str(table))
                cursor.close()
        connection.close()
    except Exception as ex:
        is_processed = False
        logger.error('Failed to create or update tables/data:'+str(ex))
    return is_processed


def create_tables(connection):
    is_processed = True
    try:
        cur = connection.cursor()
        customers_table = """ CREATE TABLE IF NOT EXISTS customers(customernumber INTEGER NOT NULL ENCODE lzo DISTKEY,
               customername VARCHAR(50) NOT NULL ENCODE lzo,contactlastname VARCHAR(50) NOT NULL ENCODE lzo, contactfirstname VARCHAR(50) NOT NULL ENCODE lzo,
               phone VARCHAR(50) NOT NULL ENCODE lzo, addressline1 VARCHAR(50) NOT NULL ENCODE lzo, addressline2 VARCHAR(50) ENCODE lzo,
               city VARCHAR(50) NOT NULL ENCODE lzo,state VARCHAR(50) ENCODE lzo, postalcode VARCHAR(15) ENCODE lzo,
               country VARCHAR(50) NOT NULL ENCODE lzo,salesrepemployeenumber INTEGER ENCODE lzo,
               creditlimit NUMERIC(10, 2) ENCODE lzo) SORTKEY (customernumber); """
        cur.execute(customers_table)
        employees_table = """ CREATE TABLE IF NOT EXISTS employees(employeenumber INTEGER NOT NULL ENCODE lzo DISTKEY,lastname VARCHAR(50) NOT NULL ENCODE lzo,
               firstname VARCHAR(50) NOT NULL ENCODE lzo,extension VARCHAR(10) NOT NULL ENCODE lzo,email VARCHAR(100) NOT NULL ENCODE lzo,
               officecode VARCHAR(10) NOT NULL ENCODE lzo,reportsto INTEGER ENCODE lzo,jobtitle VARCHAR(50) NOT NULL ENCODE lzo) SORTKEY (employeenumber); """
        cur.execute(employees_table)
        offices_table = """ CREATE TABLE IF NOT EXISTS offices(officecode VARCHAR(10) NOT NULL ENCODE lzo DISTKEY,city VARCHAR(50) NOT NULL ENCODE lzo,
               phone VARCHAR(50) NOT NULL ENCODE lzo,addressline1 VARCHAR(50) NOT NULL ENCODE lzo,addressline2 VARCHAR(50) ENCODE lzo,state VARCHAR(50) ENCODE lzo,
               country VARCHAR(50) NOT NULL ENCODE lzo,postalcode VARCHAR(15) NOT NULL ENCODE lzo,territory VARCHAR(10) NOT NULL ENCODE lzo) SORTKEY
        (officecode); """
        cur.execute(offices_table)
        order_details_table = """ CREATE TABLE IF NOT EXISTS orderdetails(ordernumber INTEGER NOT NULL ENCODE lzo DISTKEY,productcode VARCHAR(15) NOT NULL ENCODE lzo,
               quantityordered INTEGER NOT NULL ENCODE lzo,priceeach NUMERIC(10, 2) NOT NULL ENCODE lzo,orderlinenumber SMALLINT NOT NULL ENCODE lzo
        ) SORTKEY(ordernumber,productcode); """
        cur.execute(order_details_table)
        orders_table = """ CREATE TABLE IF NOT EXISTS orders(ordernumber INTEGER NOT NULL ENCODE lzo DISTKEY,
               orderdate DATE NOT NULL ENCODE lzo,requireddate DATE NOT NULL ENCODE lzo,shippeddate DATE ENCODE lzo,status VARCHAR(15) NOT NULL ENCODE lzo,
               comments VARCHAR(256) ENCODE lzo,customernumber INTEGER NOT NULL ENCODE lzo,current_year VARCHAR(5) ENCODE lzo ) SORTKEY(ordernumber); """
        cur.execute(orders_table)
        payments_table = """ CREATE TABLE IF NOT EXISTS payments(
               customernumber INTEGER NOT NULL ENCODE lzo DISTKEY, checknumber VARCHAR(50) NOT NULL ENCODE lzo,
               paymentdate DATE NOT NULL ENCODE lzo,amount NUMERIC(10, 2) NOT NULL ENCODE lzo ) SORTKEY(customernumber,checknumber); """
        cur.execute(payments_table)
        product_lines_table = """ CREATE TABLE IF NOT EXISTS productlines(productline VARCHAR(50) NOT NULL ENCODE lzo DISTKEY,textdescription VARCHAR(4000) ENCODE lzo,
               htmldescription VARCHAR(256) ENCODE lzo,image VARCHAR(256) ENCODE lzo) SORTKEY(productline); """
        cur.execute(product_lines_table)
        products_table = """ CREATE TABLE IF NOT EXISTS products(productcode VARCHAR(15) NOT NULL ENCODE lzo DISTKEY,productname VARCHAR(70) NOT NULL ENCODE lzo,productline VARCHAR(50) NOT NULL ENCODE lzo,productscale VARCHAR(100) NOT NULL ENCODE lzo,productvendor VARCHAR(50) NOT NULL ENCODE lzo,productdescription VARCHAR(65535) NOT NULL ENCODE lzo,
               quantityinstock SMALLINT NOT NULL ENCODE lzo,buyprice NUMERIC(10, 2) NOT NULL ENCODE lzo,msrp NUMERIC(10, 2) NOT NULL ENCODE lzo) SORTKEY(productcode); """
        cur.execute(products_table)
        connection.commit()
        cur.close()
    except Exception as ex:
        is_processed = False
        logger.error('Failed to create tables' + str(ex))
    return is_processed

    #connection.close()


def save_customers_data(csv_data, cursor, connection):
    customers = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 13:
            if row[11].lower() == 'null' or row[11] == '':
                row[11] = None
            data = {'customernumber': row[0], 'customername': row[1], \
                    'contactlastname': row[2], \
                    'contactfirstname': row[3], \
                    'phone': row[4], 'addressline1': row[5], 'addressline2': row[6], 'city': row[7], 'state': row[8],
                    'postalcode': row[9], 'country': row[10], 'salesrepemployeenumber': row[11], 'creditlimit': row[12]}
            customers.append(data)
        else:
            count = count + 1
    query = """ insert into customers(customernumber,customername,contactlastname,contactfirstname,phone,addressline1,addressline2,city,state,postalcode,country,salesrepemployeenumber,creditlimit)
               values (%(customernumber)s,%(customername)s,%(contactlastname)s,%(contactfirstname)s,%(phone)s,%(addressline1)s,%(addressline2)s,%(city)s,%(state)s,%(postalcode)s,%(country)s,%(salesrepemployeenumber)s,%(creditlimit)s)  """
    cursor.executemany(query, customers)
    connection.commit()


def save_offices_data(csv_data, cursor, connection):
    offices = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 9:
            data = {'officecode': row[0], 'city': row[1], \
                    'phone': row[2], \
                    'addressline1': row[3], \
                    'addressline2': row[4], 'state': row[5], 'country': row[6], 'postalcode': row[7],
                    'territory': row[8]}
            offices.append(data)
        else:
            count = count + 1
    query = """insert into offices(officecode,city,phone,addressline1,addressline2,state,country,postalcode,territory) values (%(officecode)s,%(city)s,%(phone)s,%(addressline1)s,%(addressline2)s,%(state)s,%(country)s,%(postalcode)s,%(territory)s) """
    cursor.executemany(query, offices)
    connection.commit()


def save_employees_data(csv_data, cursor, connection):
    employees = []
    count = 1
    for row in csv_data :
        if count > 1 and len(row) >= 8:
            if isinstance(row[6], str):
                row[6] = None
            data = {'employeenumber': row[0], 'lastname': row[1], 'firstname': row[2], 'extension': row[3],
                    'email': row[4], 'officecode': row[5], 'reportsto': row[6], 'jobtitle': row[7]}
            employees.append(data)
        else:
            count = count + 1
    query = """ insert into employees(employeenumber,lastname,firstname,extension,email,officecode,reportsto,jobtitle) values (%(employeenumber)s,%(lastname)s,%(firstname)s,%(extension)s,%(email)s,%(officecode)s,%(reportsto)s,%(jobtitle)s) """
    cursor.executemany(query, employees)
    connection.commit()


def save_orderdetails_data(csv_data, cursor, connection):
    orderdetails = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 5:
            data = {'ordernumber': row[0], 'productcode': row[1], 'quantityordered': row[2], 'priceeach': row[3],
                    'orderlinenumber': row[4]}
            orderdetails.append(data)
        else:
            count = count + 1
    query = """insert into orderdetails(ordernumber,productcode,quantityordered,priceeach,orderlinenumber) values (%(ordernumber)s,%(productcode)s,%(quantityordered)s,%(priceeach)s,%(orderlinenumber)s)"""
    cursor.executemany(query, orderdetails)
    connection.commit()


def save_payments_data(csv_data, cursor, connection):
    payments = []
    for row in csv_data:
        if len(row) >= 4:
            b = row[2]
            d = dateutil.parser.parse(b).date()
            data = {'customernumber': row[0], 'checknumber': row[1], 'paymentdate': d, 'amount': row[3]}
            payments.append(data)
    query = """ insert into payments(customernumber ,checknumber ,paymentdate ,amount) values (%(customernumber)s,%(checknumber)s,%(paymentdate)s,%(amount)s) """
    cursor.executemany(query, payments)
    connection.commit()


def save_orders_data(csv_data, cursor, connection):
    orders = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 8:
            row2 = row[2]
            row2d = dateutil.parser.parse(row2).date()
            row3 = row[3]
            row3d = ''
            if row3.lower() == 'null':
                row3d = None
            else:
                row3d = dateutil.parser.parse(row3).date()
            row1 = row[1]
            row1d = ''
            if row1.lower() == 'null':
                row1d = None
            else:
                row1d = dateutil.parser.parse(row1).date()
            data = {'ordernumber': row[0], 'orderdate': row1d, 'requireddate': row2d, 'shippeddate': row3d,
                    'status': row[4], 'comments': row[5], 'customernumber': row[6], 'current_year': row[7]}
            orders.append(data)
            logger.info('Order Date ..+++++++++' + row[1])
        else:
            count = count + 1
    query = """ insert into orders(ordernumber,orderdate,requireddate,shippeddate,status,comments,customernumber,current_year) values (%(ordernumber)s,%(orderdate)s,%(requireddate)s,%(shippeddate)s,%(status)s,%(comments)s,%(customernumber)s,%(current_year)s) """
    cursor.executemany(query, orders)
    connection.commit()


def save_productlines_data(csv_data, cursor, connection):
    productlines = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 4:
            data = {'productline': row[0], 'textdescription': row[1], 'htmldescription': row[2], 'image': row[3]}
            productlines.append(data)
        else:
            count = count + 1

    query = """insert into productlines(productline,textdescription,htmldescription,image) values (%(productline)s,%(textdescription)s,%(htmldescription)s,%(image)s)"""
    cursor.executemany(query, productlines)
    connection.commit()


def save_products_data(csv_data, cursor, connection):
    products = []
    count = 1
    for row in csv_data:
        if count > 1 and len(row) >= 9:
            data = {'productcode': row[0], 'productname': row[1], 'productline': row[2], 'productscale': row[3],
                    'productvendor': row[4], 'productdescription': row[5], 'quantityinstock': row[6],
                    'buyprice': row[7], 'msrp': row[8]
                    }
            products.append(data)
        else:
            count = count + 1
    query = """insert into products(productcode,productname,productline,productscale,productvendor,productdescription,quantityinstock,buyprice,msrp) values (%(productcode)s,%(productname)s,%(productline)s,%(productscale)s,%(productvendor)s,%(productdescription)s,%(quantityinstock)s,%(buyprice)s,%(msrp)s)"""
    cursor.executemany(query, products)
    connection.commit()


def delete_cluster(cluster_id):
    is_delete = False
    client = boto3.client('redshift')
    response = client.delete_cluster(
        ClusterIdentifier=cluster_id,
        SkipFinalClusterSnapshot=True)
    if response['Cluster']['ClusterStatus'] == 'deleting':
        is_delete = True
    return is_delete




