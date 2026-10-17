"""
Lambda entry point for the existing FastAPI app (api/main.py) - no route/logic changes,
Mangum just adapts API Gateway's event/response shape to/from the ASGI interface
FastAPI already speaks. Optional deployment target for the API (see README's
architecture section); the live site itself is static and doesn't use it.

Handler path for the Lambda console/Terraform config: api.lambda_handler.handler
"""
from mangum import Mangum

from api.main import app

handler = Mangum(app)
