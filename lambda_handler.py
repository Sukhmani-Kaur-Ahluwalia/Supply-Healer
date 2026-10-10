"""AWS Lambda adapter. Install mangum and configure handler as app.lambda_handler.handler."""
from mangum import Mangum
from app.main import app
handler = Mangum(app)
