import os
import logging
import json
import boto3
import psycopg2
import base64
from datetime import datetime
from ParamFormatter import ParamFormatter
import datetime

host = os.environ['HOST']
port = os.environ['PORT']
user = os.environ['USERNAME']
password = os.environ['PASSWORD']
db_name = os.environ['DBNAME']
intent_prefix = os.environ['INTENTPREFIX']

if intent_prefix and not intent_prefix.endswith('_'):
    intent_prefix = intent_prefix + '_'
file_name = 'AWSQuickStart.py'

logger = logging.getLogger('AG_DEMO_CHATBOT')
logger.setLevel('INFO')
ch = logging.StreamHandler()

if 'LOG_LEVEL' in os.environ and os.environ['LOG_LEVEL'] == 'WARNING':
    logger.setLevel('WARNING')
    ch.setLevel('WARNING')
elif 'LOG_LEVEL' in os.environ and os.environ['LOG_LEVEL'] == 'DEBUG':
    logger.setLevel('DEBUG')
    ch.setLevel('DEBUG')
elif 'LOG_LEVEL' in os.environ and os.environ['LOG_LEVEL'] == 'ERROR':
    logger.setLevel('ERROR')
    ch.setLevel('ERROR')
elif 'LOG_LEVEL' in os.environ and os.environ['LOG_LEVEL'] == 'CRITICAL':
    logger.setLevel('CRITICAL')
    ch.setLevel('CRITICAL')

is_continue = True
param_formatter = ''
sales_list = ["sales people", "sales folks", "sales person", "sales men", "sales", "salesmen", "sales teams",
              "sales team", "salesteam", "salesteams"]
product_list = ['products', 'product']
performance_list = ["top", "max", "highest", "maximum", "best", "great", "super", "awesome", "dominant", "leading",
                    "lead", "elite", "excellent", "outstanding", "terrific", "utmost"]
orders_revenue = ['order', 'orders', 'revenue', 'sales', 'business']
average_revenue_type = ["mean", "monthly", "average", "month"]
invalid_revenue_type = ["month-wise", "weekly", "week-wise", "bimonthly", "quarterly", "quarter-wise", "quarter",
                        "half-yearly", "half"]
overall_revenue_type = ["overall", "total", "ytd", "current", "many", "much", "yearly", "annual"]
revenue_type = ["mean", "monthly", "average", "month-wise", "month wise", "month", "overall", "total", "ytd", "current",
                "many", "much", "weekly", "week-wise", "bimonthly", "quarterly", "quarter-wise", "quarter",
                "half-yearly", "half", "annual", "yearly"]
revenue_slot = ['revenue', 'sales', 'business']
orders_slot = ['orders', 'order']
product_sales_list = ["products", "product", "sales people", "sales folks", "sales person", "sales men", "sales",
                      "salesmen", "sales teams", "sales team", "salesteam", "salesteams"]
team_size = ["count", "size", "sum", "big", "strength", "age", "gender", "sex", "ethnic", "ethnicity", "culture",
             "race", "racial", "religion", "number"]


# lambda execution started
def lambda_handler(event, context):
    logger.info("Requested Event is ::" + str(event))
    response = process_request(event)
    return response


# getting database connection
def get_redshift_connection():
    is_continue = True
    function_name = "get_redshift_connection"
    try:
        db_connection = psycopg2.connect(dbname=db_name, host=host, port=port, user=user, password=password)
        return db_connection
    except Exception as ex:
        is_continue = False
        error_details = "[" + file_name + "] [" + function_name + "] [Exception] in connecting to the database-[" + str(
            ex) + "]"
        logger.error(datetime.now().isoformat() + "::" + error_details)


# Decode encrypted values
def decoded_value(kms_client, encrypted_value, key):
    function_name = 'decoded_value'
    decoded_value = ''
    try:
        binary_data = base64.b64decode(encrypted_value)
        meta = kms_client.decrypt(CiphertextBlob=binary_data)
        plain_text = meta[u'Plaintext']
        decoded_value = plain_text.decode()
    except Exception as ex:
        is_continue = False
        error_details = "[" + file_name + "] [" + function_name + "] [Exception] in KMS decrypt of " + key + " to access database-[" + str(
            ex) + "]"
        logger.error(datetime.now().isoformat() + "::" + error_details)

    return decoded_value


# process user request by intent
def process_request(event):
    response_type = 'table'
    logger.info(
        'dispatch userId={}, intentName={}'.format(event['userId'], event['currentIntent']['name']))
    intent_name = event['currentIntent']['name']
    # Dispatch to your bot's intent handlers
    param_formatter = ParamFormatter()
    slots = get_lowercase_values(event['currentIntent'])
    if 'orders_revenue' in slots and slots['orders_revenue'] == 'order':
        slots['orders_revenue'] = 'orders'
    event['currentIntent']['slots'] = slots
    slots['currentIntent'] = intent_name
    response = ''
    content, is_valid = validate_slots(event['currentIntent'])
    if not is_valid:
        response = invalid_slots_response(slots, content, param_formatter)
    elif intent_name == intent_prefix+'sales_team_size_office':
        response = get_sales_team_size_office(response_type, slots)
    elif intent_name == intent_prefix+'sales_metrics_performing':
        if slots['product_salesteams'] in sales_list:
            if slots['orders_revenue'] is None:
                slots['orders_revenue'] = 'revenue'
            response = get_top_sales_person(param_formatter, response_type, slots)
        else:
            response = get_top_products_revenue_orders(param_formatter, response_type, slots)
    elif intent_name == intent_prefix+'sales_metrics_general':
        revenue_type_array = get_splited_revenue_type(slots)
        if len(set(revenue_type_array).intersection(average_revenue_type)) > 0:
            response = get_average_revenue_orders(param_formatter, slots)
        elif len(set(revenue_type_array).intersection(overall_revenue_type)) > 0:
            response = get_total_revenue_orders(param_formatter, slots)
    elif intent_name == intent_prefix+'help':
        response = get_help()
    return response


# revenue type custom slot filtter
def get_splited_revenue_type(slots):
    revenue_type_array = []
    if slots['revenue_type'] is not None:
        revenue_type_array = slots['revenue_type'].split()
    return revenue_type_array


# convert values to lower case
def get_lowercase_values(current_intent):
    slots = {}
    if 'slots' in current_intent:
        slots = {k: v.lower() if isinstance(v, str) else v for k, v in current_intent['slots'].items()}
    return slots


# get default response
def get_default_response(content):
    response = {
        "sessionAttributes": {
            "card": ""
        },
        "dialogAction": {
            "type": "Close",
            "fulfillmentState": "Fulfilled",
            "message": {
                "contentType": "PlainText",
                "content": content
            }
        }
    }
    return response


# validating slots
def validate_slots(current_intent):
    content = ''
    is_valid = True
    year = datetime.date.today().year
    slots = current_intent['slots']
    intent_name = current_intent['name']
    if intent_name == intent_prefix+'sales_metrics_general':
        content, is_valid = validate_sales_metrics_general_slots(content, is_valid, slots, year)
    elif intent_name == intent_prefix+'sales_metrics_performing':
        content, is_valid = validate_sales_metrics_performing_slots(content, is_valid, slots, year)
    elif intent_name == intent_prefix+'sales_team_size_office':
        content, is_valid = validate_sales_team_size_office_slots(content, is_valid, slots, year)

    return content, is_valid


# validate sales metric general slots
def validate_sales_metrics_general_slots(content, is_valid, slots, year):
    if slots['year'] is not None and slots['orders_revenue'] is None and slots['revenue_type'] is None:
        is_valid = False
        content = "I couldn't understand what you mean. Type \"Help\" to know about the areas that I can converse on."
    elif slots['year'] is not None and int(slots['year'].split('-')[0]) != int(year):
        is_valid = False
        slots['current_year'] = year
        content = "Oops! I have only current year data with me. Please ask for a metric on current year."
    elif slots['revenue_type'] is None:
        is_valid = False
        content = " I am having trouble understanding what you mean. I have data only on <b>overall or average</b> revenue and orders."
    elif slots['revenue_type'] is not None and (
            len(set(get_splited_revenue_type(slots)).intersection(average_revenue_type)) == 0 and len(
            set(get_splited_revenue_type(slots)).intersection(overall_revenue_type)) == 0):
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on <b>overall or average</b> revenue and orders."
    elif slots['revenue_type'] is not None and slots['revenue_type'] in invalid_revenue_type:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on <b>overall or average</b> revenue and orders."
    elif slots['orders_revenue'] is None:
        is_valid = False
        content = "I am having trouble understanding what you mean. I have data only on <b>revenue or orders</b>."
    elif slots['orders_revenue'] is not None and slots['orders_revenue'] not in orders_revenue:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on <b>revenue or orders</b>."

    return content, is_valid


# validate sales metric performance slots
def validate_sales_metrics_performing_slots(content, is_valid, slots, year):
    if slots['year'] is not None and slots['orders_revenue'] is None and slots['performance_type'] is None and slots[
        'product_salesteams'] is None:
        is_valid = False
        content = "I couldn't understand what you mean. Type \"Help\" to know about the areas that I can converse on."
    elif slots['year'] is not None and int(slots['year'].split('-')[0]) != int(year):
        is_valid = False
        slots['current_year'] = year
        content = "Oops! I have only current year data with me. Please ask for a metric on current year."
    elif slots['performance_type'] is None:
        is_valid = False
        content = "I am having trouble understanding what you mean. I have data only on <b>top</b> performing products or sales teams."
    elif slots['performance_type'] is not None and slots['performance_type'] not in performance_list:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on <b>top</b> performing products or sales teams."
    elif slots['product_salesteams'] is None:
        is_valid = False
        content = "I am having trouble understanding what you mean. I have data only on top performing <b>products or sales teams</b>."
    elif slots['product_salesteams'] is not None and slots['product_salesteams'] not in product_sales_list:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on top performing <b>products or sales teams</b>."
    elif slots['orders_revenue'] is not None and slots['orders_revenue'] not in orders_revenue:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on top performing products or sales teams by <b>revenue or orders</b>"
    elif slots['orders_revenue'] is None:
        slots['orders_revenue'] = 'revenue'
    return content, is_valid


# validate sales team size slots
def validate_sales_team_size_office_slots(content, is_valid, slots, year):
    if slots['year'] is not None and slots['orders_revenue'] is None and slots['revenue_type'] is None:
        is_valid = False
        content = "I couldn't understand what you mean. Type \"Help\" to know about the areas that I can converse on."
    elif slots['year'] is not None and int(slots['year'].split('-')[0]) != int(year):
        is_valid = False
        slots['current_year'] = year
        content = "Oops! I have only current year data with me. Please ask for a metric on current year."
        is_valid = False
        content = "I am having trouble understanding what you mean. I have data only on <b>size or count</b> of sales team by each office.."
    elif slots['team_size'] is not None and slots['team_size'] not in team_size:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on <b>size or count</b> of sales team by each office."
    elif slots['product_salesteams'] is None:
        is_valid = False
        content = "I am having trouble understanding what you mean. I have data only on size or count of <b>sales team by sales offices</b>."
    elif slots['product_salesteams'] is not None and slots['product_salesteams'] not in product_sales_list:
        is_valid = False
        content = "Oops! I can't answer that. At present, I have data only on size or count of <b>sales team by sales offices</b>."

    return content, is_valid


# preparing response for invalid slots
def invalid_slots_response(slots, content, param_formatter):
    response = {
        "sessionAttributes": {
            "card": ""
        },
        "dialogAction": {
            "type": "Close",
            "fulfillmentState": "Fulfilled",
            "message": {
                "contentType": "PlainText",
                "content": param_formatter.format(content, **slots)
            }
        }
    }
    return response


# Get help content
def get_help():
    response = {
        "sessionAttributes": {
            "card": json.dumps({"help": ["YTD revenue", "YTD orders", "Average revenue", "Average orders",
                                         "Top selling products by revenue", "Top selling products by orders",
                                         "Top performing sales teams", "Sales team size by office"]})
        },
        "dialogAction": {
            "type": "Close",
            "fulfillmentState": "Fulfilled",
            "message": {
                "contentType": "PlainText",
                "content": """I can answer these questions for you:"""
            }
        }
    }
    return response


# Get average revenue/orders
def get_average_revenue_orders(param_formatter, slots):
    if slots['orders_revenue'] in revenue_slot:
        slots['orders_revenue'] = 'revenue'
        content = "We made an average {orders_revenue} of ${Average_Revenue_Orders} each month. This is excluding the revenue from current month."
    elif slots['orders_revenue'] in orders_slot:
        content = "We received an average {orders_revenue} of {Average_Revenue_Orders} each month. This is excluding the orders from current month."
    query = """select case when to_char(current_date,'MM') != '01' then average_revenue_orders else '422' end 
               as average_revenue_orders from (select  case when 'revenue' = '{orders_revenue}' then avg(revenue) else avg(orders) end ::real as average_revenue_orders   
               from (select  sum(od.quantityordered*od.priceeach)/(to_char(current_date,'MM')-1) as revenue ,count(od.ordernumber) as orders, to_char(o.orderdate::date,'MM') as Months 
               from public.orderdetails od,public.orders o where od.ordernumber = o.ordernumber and current_year = to_char(current_date,'yyyy')  and
               to_char(o.orderdate::date,'MM') < to_char(current_date,'MM') group by Months ))"""
    dynamic_query = param_formatter.format(query, **slots)
    response = prepare_response(dynamic_query, 'text', content, slots)
    return response


# Get total revenue/orders
def get_total_revenue_orders(param_formatter, slots):
    if slots['orders_revenue'] in revenue_slot:
        slots['orders_revenue'] = 'revenue'
        content = "The YTD {orders_revenue} for this year is ${Revenue_Orders}. "
    else:
        content = "The YTD {orders_revenue} for this year is {Revenue_Orders}. "
    query = """select case when 'revenue' = '{orders_revenue}' then  sum(ord.quantityordered*ord.priceeach) else count(ord.ordernumber) end ::real as revenue_orders 
    from public.products p ,public.orderdetails ord,public.orders o where p.productcode = ord.productcode  and ord.ordernumber = o.ordernumber and current_year = to_char(current_date,'yyyy') 
    and to_Date(o.orderdate,'DD-MM') < to_Date(current_Date,'DD-MM') """
    dynamic_query = param_formatter.format(query, **slots)
    response = prepare_response(dynamic_query, 'text', content, slots)

    return response


# Get top sales person each office
def get_top_sales_person(param_formatter, response_type, slots):
    content = 'Here are the list of top sales person by each office.'
    query = """ with EMP as (select ofc.city  \"Sales Office\" ,e.firstname || ' '||e.lastname  \"Top Sales Person\",
               case when 'revenue' = '{orders_revenue}' then sum(ord.quantityordered*ord.priceeach)::real else 
               count(ord.ordernumber) end  \"{orders_revenue}\" from   public.offices ofc ,public.employees e,public.customers c ,
                public.orders o ,public.orderdetails ord where ofc.officecode = e.officecode and e.employeenumber=c.salesrepemployeenumber 
                and c.customernumber = o.customernumber and o.ordernumber = ord.ordernumber and current_year = to_char(current_date,'yyyy') and to_Date(o.orderdate,'DD-MM') < to_Date(current_Date,'DD-MM')
                group by \"Sales Office\",\"Top Sales Person\") select \"Sales Office\",\"Top Sales Person\", \"{orders_revenue}\" 
                from EMP where (\"{orders_revenue}\",\"Sales Office\") in  (select max(\"{orders_revenue}\"), \"Sales Office\" from EMP group by  "Sales Office")
                order by \"Sales Office\" """
    dynamic_query = param_formatter.format(query, **slots)
    response = prepare_response(dynamic_query, response_type, content, slots)
    return response


# Get top revenue/orders by products
def get_top_products_revenue_orders(param_formatter, response_type, slots):
    if slots['orders_revenue'] in revenue_slot:
        slots['orders_revenue'] = 'revenue'
        content = 'Here are the top product lines by revenue.'
    else:
        content = 'Here are the top product lines by orders.'
    query = """ select  p.productline \"Product Line\",case when 'revenue' = '{orders_revenue}' then sum(ord.quantityordered*ord.priceeach) 
    else count(ord.ordernumber)::real end as {orders_revenue} from  public.products p ,public.orderdetails ord , public.orders o 
    where p.productcode = ord.productcode  and ord.ordernumber = o.ordernumber and current_year = to_char(current_date,'yyyy') 
    and to_Date(o.orderdate,'DD-MM') < to_Date(current_Date,'DD-MM') group by \"Product Line\" order by {orders_revenue}  desc """
    dynamic_query = param_formatter.format(query, **slots)
    response = prepare_response(dynamic_query, response_type, content, slots)

    return response


# Get sales team size office
def get_sales_team_size_office(response_type, slots):
    content = 'Here is the sales team size by office.'
    query = """select ofc.city \"sales office\", count(employeenumber)   \"Sales Team Size\" from public.employees e ,public.offices ofc where e.officecode =  ofc.officecode group by \"sales office\" order by   \"sales office\" """
    response = prepare_response(query, response_type, content, slots)
    return response


# preparing response object
def prepare_response(query, response_type, content, slots):
    response = ''
    logger.info('Generated Query :: ' + str(query))
    # once query ready need to enable the db connection and close it
    db_connection = get_redshift_connection()
    db_cursor = db_connection.cursor() 
    if is_continue:
        try:
            db_cursor.execute(query)
            rows = db_cursor.fetchall()
            column_names_pre_title = [desc[0] for desc in db_cursor.description]
            column_names = [t.title() for t in column_names_pre_title]
            result = []
            param_formatter = ParamFormatter()
            if response_type == 'text':
                response = get_text_response(column_names, content, param_formatter, rows, slots)
            elif response_type == 'table' and len(rows) > 0:
                response = get_table_response(column_names, content, param_formatter, response_type, result, rows,slots)
            else:
                content = "Sorry, I don't seem to find information related to your query in the database."
                response = get_default_response(content)
          
        except Exception as ex:
            logger.error('Exception while trying to execute query :: ' + str(ex))
            content = "Sorry, I am having trouble connecting to the database at the moment. Please try after sometime. If the problem persists, contact IT support."
            response = get_default_response(content)
            return response
        finally:
            if db_cursor != None:
                db_cursor.close()
            if db_connection != None:
                db_connection.close()
    else:
        # default response.
        logger.debug("Default Response")
        response = {
            "sessionAttributes": {
                "card": ""
            },
            "dialogAction": {
                "type": "Close",
                "fulfillmentState": "Fulfilled",
                "message": {
                    "contentType": "PlainText",
                    "content": "Sorry, I am having trouble connecting to the database at the moment. Please try after sometime. If the problem persists, contact IT support."
                }

            }}

    logger.debug("Response is : " + str(response))
    # return response["Response"]
    return response


# preparing table response
def get_table_response(column_names, content, param_formatter, response_type, result, rows, slots):
    result = []
    for row in rows:
        item = create_record(row, column_names)
        if 'Revenue' in item:
            if item['Revenue'] > 100000:
                item['Revenue'] = '$' + format(int(round(item['Revenue'] / 1000)), ',d') + 'K'
            else:
                item['Revenue'] = '$' + format(int(round(item['Revenue'])), ',d')
        if 'Orders' in item:
            item['Orders'] = format(int(round(item['Orders'])), ',d')

        result.append(item)
    if len(result) > 0:
        total = round(sum(i[-1:][0] for i in rows))
        if 'Revenue' in column_names:
            if total > 100000:
                if len(column_names) == 3:
                    result.append({column_names[0]: "Total", column_names[1]: "",
                                   column_names[2]: '$' + format(int(round(total / 1000)), ',d') + 'K'})
                elif len(column_names) == 2:
                    result.append(
                        {column_names[0]: "Total", column_names[1]: '$' + format(int(round(total / 1000)), ',d') + 'K'})
            else:
                if len(column_names) == 3:
                    result.append({column_names[0]: "Total", column_names[1]: "",
                                   column_names[2]: '$' + format(int(round(total)), ',d')})
                elif len(column_names) == 2:
                    result.append({column_names[0]: "Total", column_names[1]: '$' + format(int(round(total)), ',d')})
        elif 'Orders' in column_names:
            if len(column_names) == 3:
                result.append(
                    {column_names[0]: "Total", column_names[1]: "", column_names[2]: format(int(round(total)), ',d')})
            elif len(column_names) == 2:
                result.append({column_names[0]: "Total", column_names[1]: format(int(round(total)), ',d')})
        elif 'Sales Team Size' in column_names:
            if len(column_names) == 3:
                result.append(
                    {column_names[0]: "Total", column_names[1]: "", column_names[2]: format(int(round(total)), ',d')})
            elif len(column_names) == 2:
                result.append({column_names[0]: "Total", column_names[1]: format(int(round(total)), ',d')})

    logger.debug("Result : " + str(result))
    response = {
        "sessionAttributes": {
            "card": json.dumps({response_type: result})
        },
        "dialogAction": {
            "type": "Close",
            "fulfillmentState": "Fulfilled",
            "message": {
                "contentType": "PlainText",
                "content": param_formatter.format(content, **slots)
            }
        }
    }
    return response


# preparing text response
def get_text_response(column_names, content, param_formatter, rows, slots):
    if rows[0][0] is None:
        content = "Sorry, I don't seem to find information related to your query in the database."
    elif rows[0][0] == 422:
        content = "We are just in the month of January. I cant get you the details yet."
    elif 'orders_revenue' in slots and slots['orders_revenue'] == 'revenue':
        row = float(rows[0][0])
        if row > 100000:
            slots[column_names[0]] = format(int(round(row / 1000)), ',d') + 'K'
        else:
            slots[column_names[0]] = format(int(round(row)), ',d')
    elif 'orders_revenue' in slots and slots['orders_revenue'].lower() == 'orders':
        slots[column_names[0]] = format(int(round(rows[0][0])), ',d')

    response = {
        "sessionAttributes": {
            "card": ""
        },
        "dialogAction": {
            "type": "Close",
            "fulfillmentState": "Fulfilled",
            "message": {
                "contentType": "PlainText",
                "content": param_formatter.format(content, **slots)
            }
        }
    }
    return response


# preparing mapping based on db columns and data
def create_record(obj, fields):
    mappings = dict(zip(fields, obj))
    return mappings
