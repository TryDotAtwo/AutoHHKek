from autohhkek.services.storage import WorkspaceStore


def test_workspace_store_isolates_account_specific_files(tmp_path):
    primary = WorkspaceStore(tmp_path, account_key="hh-primary")
    primary.save_hh_resumes([{"resume_id": "resume-a", "title": "Primary"}])
    primary.save_selected_resume_id("resume-a")
    primary.save_dashboard_state({"marker": "primary"})
    primary.save_account_profile({"account_key": "hh-primary", "display_name": "Primary"})

    secondary = WorkspaceStore(tmp_path, account_key="hh-secondary")
    secondary.save_hh_resumes([{"resume_id": "resume-b", "title": "Secondary"}])
    secondary.save_selected_resume_id("resume-b")
    secondary.save_dashboard_state({"marker": "secondary"})
    secondary.save_account_profile({"account_key": "hh-secondary", "display_name": "Secondary"})

    assert WorkspaceStore(tmp_path, account_key="hh-primary").load_hh_resumes()[0]["resume_id"] == "resume-a"
    assert WorkspaceStore(tmp_path, account_key="hh-primary").load_selected_resume_id() == "resume-a"
    assert WorkspaceStore(tmp_path, account_key="hh-primary").load_dashboard_state()["marker"] == "primary"

    assert WorkspaceStore(tmp_path, account_key="hh-secondary").load_hh_resumes()[0]["resume_id"] == "resume-b"
    assert WorkspaceStore(tmp_path, account_key="hh-secondary").load_selected_resume_id() == "resume-b"
    assert WorkspaceStore(tmp_path, account_key="hh-secondary").load_dashboard_state()["marker"] == "secondary"

    active = WorkspaceStore(tmp_path)
    assert active.account_key == "hh-secondary"
    assert len(active.load_accounts()) == 2
