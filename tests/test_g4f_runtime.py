from autohhkek.services.g4f_runtime import G4FAppConfig


def test_g4f_runtime_resolves_to_verified_no_auth_target():
    config = G4FAppConfig(model="gpt-4o-mini")

    resolved = config.resolve_target()

    assert resolved is not None
    assert resolved["model"] == "gpt-4o-mini"
    assert resolved["provider"]
    assert resolved["working"] is True
    assert resolved["needs_auth"] is False


def test_g4f_runtime_falls_back_when_requested_target_is_not_verified():
    config = G4FAppConfig(model="deepseek-v3")

    resolved = config.resolve_target()

    assert resolved is not None
    assert resolved["model"] != "deepseek-v3"
    assert resolved["needs_auth"] is False


def test_g4f_runtime_rejects_unknown_provider_for_known_model():
    config = G4FAppConfig(model="gpt-4o-mini", provider="DefinitelyMissingProvider")

    resolved = config.resolve_target()

    assert resolved is not None
    assert resolved["provider"] != "DefinitelyMissingProvider"


def test_g4f_runtime_exposes_verified_targets_catalog():
    targets = G4FAppConfig().available_targets()

    assert targets
    assert all(target["working"] for target in targets)
    assert all(target["needs_auth"] is False for target in targets)
