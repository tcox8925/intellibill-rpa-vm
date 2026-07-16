from typing import Dict, Any, Optional
from ai_intent_parser import parse_with_llm, IntentParseError
from resolver import Resolver
from intent_models import QueryIntent
from semantic_registry import MODULES
from db_executor import get_postgres_connection,get_synapse_connection


class ClarificationOrchestrator:

    def __init__(self):
        self.resolver = None

    # -------------------------------------------------------
    # Validation
    # -------------------------------------------------------

    def _validate_intent(self, intent: QueryIntent):

        if intent.module not in MODULES:
            raise ValueError("Invalid module")

        module_cfg = MODULES[intent.module]

        if intent.metric not in module_cfg["metrics"]:
            raise ValueError("Invalid metric")

        for dim in intent.dimensions:
            if dim not in module_cfg["dimensions"]:
                raise ValueError(f"Invalid dimension: {dim}")

        for f in intent.filters.keys():
            if f not in module_cfg["filters"]:
                raise ValueError(f"Invalid filter: {f}")

        if intent.state_dimension:
            if intent.state_dimension not in module_cfg.get("state_expansion", {}):
                raise ValueError("Invalid state dimension")

    # -------------------------------------------------------
    # Filter Normalization
    # -------------------------------------------------------

    def _normalize_filters(self, intent: QueryIntent):

        filters = intent.filters

        # Carrier logical routing
        if "carrier" in filters:
            value = filters["carrier"]

            if isinstance(value, str):
                if value.isdigit():
                    filters["carrier_id"] = value
                else:
                    filters["carrier_name"] = value

            del filters["carrier"]


        # Agent and Member left for dynamic resolution

    # -------------------------------------------------------
    # Entity + Categorical Resolution
    # -------------------------------------------------------

    def _resolve_entities(
            self,
            intent: QueryIntent,
            resolved_filters: Optional[Dict[str, str]]
    ) -> Dict[str, Any]:

        module_cfg = MODULES[intent.module]
        filters_cfg = module_cfg["filters"]
        entity_links = module_cfg.get("entity_links", {})

        # -------------------------------------------------
        # If user selected clarification option
        # -------------------------------------------------
        if resolved_filters:
            for key, value in resolved_filters.items():
                intent.filters[key] = value
            return {"resolved": True}

        # -------------------------------------------------
        # Automatic resolution
        # -------------------------------------------------
        for key, value in list(intent.filters.items()):

            filter_cfg = filters_cfg.get(key, {})
            filter_type = filter_cfg.get("type")

            if not isinstance(value, str) or not value.strip():
                continue

            # ---------------------------------------------
            # Skip logical time
            # ---------------------------------------------
            if filter_type == "logical_time":
                continue

            # ---------------------------------------------
            # DYNAMIC AGENT
            # ---------------------------------------------
            if filter_type == "dynamic_agent":

                resolution = self.resolver.resolve_entity(
                    entity_type="agent",
                    value=value,
                    limit=10
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                resolved_npn = resolution["match"]["resolve_value"]
                local_key = entity_links.get("agent", {}).get("local_key")

                if local_key:
                    del intent.filters[key]
                    intent.filters[local_key] = resolved_npn

                continue

            # ---------------------------------------------
            # DYNAMIC UPLINE
            # ---------------------------------------------
            if filter_type == "dynamic_upline":

                resolution = self.resolver.resolve_entity(
                    entity_type="agent",
                    value=value,
                    limit=10
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                resolved_npn = resolution["match"]["resolve_value"]

                del intent.filters[key]
                intent.filters["upline_npn"] = resolved_npn

                continue

            # ---------------------------------------------
            # DYNAMIC TOP UPLINE
            # ---------------------------------------------
            if filter_type == "dynamic_top_upline":

                resolution = self.resolver.resolve_entity(
                    entity_type="agent",
                    value=value,
                    limit=10
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                resolved_npn = resolution["match"]["resolve_value"]

                del intent.filters[key]
                intent.filters["top_upline_npn"] = resolved_npn

                continue

            # ---------------------------------------------
            # DYNAMIC CARRIER
            # ---------------------------------------------
            if filter_type == "dynamic_carrier":

                resolution = self.resolver.resolve_entity(
                    entity_type="carrier",
                    value=value,
                    limit=10
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                resolved_carrier = resolution["match"]["resolve_value"]

                del intent.filters[key]
                intent.filters["carrier_name"] = resolved_carrier

                continue

            # ---------------------------------------------
            # DYNAMIC MEMBER
            # ---------------------------------------------
            if filter_type == "dynamic_member":

                resolution = self.resolver.resolve_entity(
                    entity_type="commission_member",
                    value=value,
                    limit=10
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                resolved_account = resolution["match"]["resolve_value"]

                del intent.filters[key]
                intent.filters["account_number"] = resolved_account

                continue

            # ---------------------------------------------
            # CATEGORICAL
            # ---------------------------------------------
            if filter_type in ("categorical_strict", "categorical_token", "state_flag"):

                table = module_cfg["table"]
                column = filter_cfg.get("column")

                resolution = self.resolver.resolve_categorical_value(
                    table=table,
                    column=column,
                    value=value,
                    limit=100
                )

                if not resolution.get("resolved", False):
                    return {
                        "resolved": False,
                        "resolution_type": key,
                        "clarification": resolution.get("candidates"),
                    }

                intent.filters[key] = resolution["match"]["resolve_value"]

                continue

        return {"resolved": True}

    # -------------------------------------------------------
    # Main Entry
    # -------------------------------------------------------

    def process(
        self,
        question: str,
        company_id: int,
        resolved_filters: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:

        try:
            intent, confidence = parse_with_llm(question, company_id)
            # -------------------------------------------------
            # Route DB connection based on module
            # -------------------------------------------------
            if intent.module == "bob":
                conn = get_synapse_connection()
            else:
                conn = get_postgres_connection()

            self.resolver = Resolver(conn)

        except IntentParseError as e:
            return {
                "status": "clarification_required",
                "message": f"I couldn't understand the request. {str(e)}",
                "resolution_type": None,
                "options": None
            }

        if intent.metric is None:
            return {
                "status": "clarification_required",
                "message": "I couldn't determine what metric to calculate.",
                "resolution_type": None,
                "options": None
            }

        if confidence < 0.5:
            return {
                "status": "clarification_required",
                "message": "I'm not fully confident about this request. Could you clarify?",
                "resolution_type": None,
                "options": None
            }

        self._normalize_filters(intent)

        try:
            self._validate_intent(intent)
        except ValueError as e:
            return {
                "status": "clarification_required",
                "message": str(e),
                "resolution_type": None,
                "options": None
            }

        resolution = self._resolve_entities(intent, resolved_filters)

        if not resolution["resolved"]:
            return {
                "status": "clarification_required",
                "message": "Multiple matches found. Please select one:",
                "resolution_type": resolution["resolution_type"],
                "options": resolution["clarification"]
            }

        return {"status": "resolved", "intent": intent}