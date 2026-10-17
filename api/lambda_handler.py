"""
Lambda entry point for the existing FastAPI app (api/main.py) - no route/logic changes,
Mangum just adapts API Gateway's event/response shape to/from the ASGI interface
FastAPI already speaks. This is an additional deployment target alongside Railway
(see README's architecture section), not a replacement - both point at the same app.

Handler path for the Lambda console/Terraform config: api.lambda_handler.handler
"""
from mangum import Mangum

from api.main import app

handler = Mangum(app)
