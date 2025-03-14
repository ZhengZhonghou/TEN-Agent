
import logging

# logging
FORMAT = '%(asctime)15s %(name)s-%(levelname)s  %(funcName)s:%(lineno)s %(message)s'
logging.basicConfig(level=logging.INFO, format=FORMAT)
logger = logging.getLogger('order_agent')
