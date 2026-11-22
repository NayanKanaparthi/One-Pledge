"""HTTP plumbing shared by both doubles (TEST DOUBLES).

Split by responsibility (SPEC_DECISIONS 80): `scope` holds what every part shares, `requests`
reads and validates a request, `envelope` builds the error envelope and the handlers, and
`middleware` wraps each request and assembles an app. The names the doubles use are
re-exported here.
"""

from onepledge_contract_fakes.web.envelope import error_response, failure_response, install_error_handlers
from onepledge_contract_fakes.web.middleware import RequestContextMiddleware, new_service_app
from onepledge_contract_fakes.web.requests import (
    BodyRead,
    check_test_control,
    check_test_control_key,
    details_from_pydantic,
    parse_model_json,
    read_body,
    validate_in_order,
)
from onepledge_contract_fakes.web.scope import (
    MAX_BODY_BYTES,
    REQUEST_ID_HEADER,
    TEST_CONTROL_HEADER,
    AsgiApp,
    Message,
    Receive,
    Scope,
    Send,
    contract_routes,
    request_id_of,
    route_template,
)

__all__ = [
    "MAX_BODY_BYTES",
    "REQUEST_ID_HEADER",
    "TEST_CONTROL_HEADER",
    "AsgiApp",
    "BodyRead",
    "Message",
    "Receive",
    "RequestContextMiddleware",
    "Scope",
    "Send",
    "check_test_control",
    "check_test_control_key",
    "contract_routes",
    "details_from_pydantic",
    "error_response",
    "failure_response",
    "install_error_handlers",
    "new_service_app",
    "parse_model_json",
    "read_body",
    "request_id_of",
    "route_template",
    "validate_in_order",
]
