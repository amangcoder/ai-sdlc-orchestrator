"""Tests for role registry and utilities."""

from __future__ import annotations

from orchestrator.models import AgentRole, RoleAccess, WorkflowType
from orchestrator.roles import (
    ROLE_REGISTRY,
    get_role,
    get_roles_for_workflow,
    role_to_legacy_agent_name,
    validate_role_access,
)
from orchestrator.workflows import FEATURE_DEVELOPMENT


class TestRoleRegistry:
    def test_all_18_roles_registered(self):
        assert len(ROLE_REGISTRY) == 18

    def test_all_agent_roles_have_entry(self):
        for role in AgentRole:
            assert role in ROLE_REGISTRY, f"Missing registry entry for {role}"

    def test_pm_is_read_only(self):
        defn = get_role(AgentRole.PRODUCT_MANAGER)
        assert defn.access == RoleAccess.READ_ONLY

    def test_backend_engineer_is_read_write(self):
        defn = get_role(AgentRole.BACKEND_ENGINEER)
        assert defn.access == RoleAccess.READ_WRITE

    def test_security_engineer_is_read_only(self):
        defn = get_role(AgentRole.SECURITY_ENGINEER)
        assert defn.access == RoleAccess.READ_ONLY


class TestValidateRoleAccess:
    def test_read_only_blocks_write(self):
        assert not validate_role_access(AgentRole.PRODUCT_MANAGER, is_write=True)

    def test_read_only_allows_read(self):
        assert validate_role_access(AgentRole.PRODUCT_MANAGER, is_write=False)

    def test_read_write_allows_both(self):
        assert validate_role_access(AgentRole.BACKEND_ENGINEER, is_write=True)
        assert validate_role_access(AgentRole.BACKEND_ENGINEER, is_write=False)


class TestRoleToLegacyAgentName:
    def test_pm_mapping(self):
        assert role_to_legacy_agent_name(AgentRole.PRODUCT_MANAGER) == "pm"

    def test_architect_mapping(self):
        assert role_to_legacy_agent_name(AgentRole.SOFTWARE_ARCHITECT) == "architect"

    def test_frontend_engineer_mapping(self):
        assert role_to_legacy_agent_name(AgentRole.FRONTEND_ENGINEER) == "frontend_engineer"


class TestGetRolesForWorkflow:
    def test_feature_development_roles(self):
        roles = get_roles_for_workflow(FEATURE_DEVELOPMENT)
        assert AgentRole.PRODUCT_MANAGER in roles
        assert AgentRole.SOFTWARE_ARCHITECT in roles
        assert AgentRole.BACKEND_ENGINEER in roles
        assert AgentRole.DEVOPS_ENGINEER in roles
