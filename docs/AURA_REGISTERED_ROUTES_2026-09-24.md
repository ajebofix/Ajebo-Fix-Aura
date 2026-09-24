# Aura registered routes — 24 September 2026

Generated from the Flask application after adding the Rina workspace and account-help routes. This refresh supersedes the earlier route list; the explanatory architecture notes remain applicable. Regenerate with `PYTHONPATH=. python scripts/snapshot_routes.py`.

| Methods | Route | Endpoint |
|---|---|---|
| GET | `/` | `home` |
| GET, POST | `/account/setup` | `owner_onboarding.account_setup` |
| GET | `/admin/alerts` | `admin.admin_alert_center` |
| POST | `/admin/alerts/<int:alert_id>/acknowledge` | `admin.acknowledge_alert` |
| GET | `/admin/alerts/<int:alert_id>/history` | `admin.alert_history` |
| POST | `/admin/alerts/<int:alert_id>/resolve` | `admin.resolve_alert` |
| GET, POST | `/admin/assessments/<int:assessment_id>/addenda` | `admin.admin_assessment_addenda` |
| GET | `/admin/assessments/<int:assessment_id>/download` | `admin_assessments.admin_download_assessment_pdf` |
| GET, POST | `/admin/assessments/<int:assessment_id>/edit` | `admin.admin_edit_assessment` |
| POST | `/admin/assessments/<int:assessment_id>/finalize` | `admin.admin_finalize_assessment` |
| GET | `/admin/cars/<int:car_id>` | `admin.view_vehicle` |
| POST | `/admin/cars/<int:car_id>/care-pathway` | `admin.update_care_pathway` |
| GET | `/admin/cars/<int:car_id>/concerns` | `admin.admin_vehicle_concerns` |
| GET, POST | `/admin/cars/<int:car_id>/concerns/add` | `admin.admin_add_concern` |
| GET, POST | `/admin/cars/<int:car_id>/consultations/schedule` | `admin.admin_schedule_consultation` |
| GET | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>` | `historical_ingestion.episode_detail` |
| POST | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/attribute/<int:corpus_evidence_id>` | `historical_ingestion.start_episode_attribution` |
| GET | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/attributions/<int:extraction_id>/status` | `historical_ingestion.episode_attribution_status` |
| POST | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/reconcile/<int:attribution_extraction_id>/start` | `historical_ingestion.start_episode_reconciliation_review` |
| POST | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/reconciliations/<int:extraction_id>/apply` | `historical_ingestion.apply_episode_reconciliation` |
| POST | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/reconciliations/<int:extraction_id>/review` | `historical_ingestion.save_episode_reconciliation_review` |
| GET | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/reconciliations/<int:extraction_id>/status` | `historical_ingestion.episode_reconciliation_status` |
| POST | `/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/treatment-actions/<int:treatment_action_id>/addenda` | `historical_ingestion.add_episode_treatment_action_addendum` |
| GET | `/admin/cars/<int:car_id>/historical-records` | `historical_ingestion.source_library` |
| GET | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/analysis-status` | `historical_ingestion.analysis_status` |
| POST | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/apply` | `historical_ingestion.apply_document` |
| POST | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/create-episode` | `historical_ingestion.create_historical_episode` |
| POST | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/reanalyze` | `historical_ingestion.reanalyze_document` |
| GET | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/review` | `historical_ingestion.review_document` |
| POST | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/review` | `historical_ingestion.save_review` |
| POST | `/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/supersede` | `historical_ingestion.supersede_source` |
| GET, POST | `/admin/cars/<int:car_id>/historical-records/import` | `historical_ingestion.import_document` |
| GET | `/admin/cars/<int:car_id>/historical-service-verification` | `admin.historical_service_verification` |
| POST | `/admin/cars/<int:car_id>/invite-driver` | `admin.invite_driver` |
| GET | `/admin/cars/<int:car_id>/maintenance` | `admin.maintenance_intelligence` |
| GET, POST | `/admin/cars/<int:car_id>/odometer` | `admin.update_odometer` |
| POST | `/admin/cars/<int:car_id>/odometer-reports/<int:observation_id>/accept` | `admin.accept_odometer_report` |
| POST | `/admin/cars/<int:car_id>/odometer-reports/<int:observation_id>/reject` | `admin.reject_odometer_report` |
| POST | `/admin/cars/<int:car_id>/priority-access` | `admin.toggle_priority_access` |
| POST | `/admin/cars/<int:car_id>/priority-request` | `admin.admin_create_priority_request` |
| GET | `/admin/cars/<int:car_id>/records` | `admin.admin_vehicle_records` |
| GET | `/admin/cars/<int:car_id>/records/pdf` | `admin.admin_vehicle_records_pdf` |
| POST | `/admin/cars/<int:car_id>/service-events/<int:event_id>/maintenance-classification` | `admin.classify_service_maintenance_item` |
| POST | `/admin/cars/<int:car_id>/service-events/<int:event_id>/verification-review` | `admin.review_historical_service_verification` |
| GET, POST | `/admin/cars/<int:car_id>/service/add` | `admin.admin_add_service` |
| GET | `/admin/clients` | `admin.admin_clients` |
| GET | `/admin/clients/<int:user_id>` | `admin.admin_client_profile` |
| POST | `/admin/clients/<int:user_id>/activation-link` | `owner_onboarding.regenerate_activation_link` |
| POST | `/admin/clients/<int:user_id>/notes/add` | `admin.add_advisor_note` |
| GET, POST | `/admin/clients/<int:user_id>/vehicles/new` | `owner_onboarding.add_client_vehicle` |
| GET, POST | `/admin/clients/new` | `owner_onboarding.create_client` |
| GET | `/admin/concerns` | `admin.admin_reported_concerns` |
| POST | `/admin/concerns/<int:concern_id>/monitor` | `admin.admin_monitor_concern` |
| GET | `/admin/concerns/<int:concern_id>/progression` | `concern_progression.concern_progression` |
| POST | `/admin/concerns/<int:concern_id>/resolve` | `admin.admin_resolve_concern` |
| POST | `/admin/concerns/<int:concern_id>/review` | `admin.admin_review_concern` |
| GET | `/admin/consultations` | `admin.admin_consultations` |
| POST | `/admin/consultations/<int:consultation_id>/assessment/start` | `admin.admin_start_assessment` |
| GET, POST | `/admin/consultations/<int:consultation_id>/complete` | `admin.admin_complete_consultation` |
| GET, POST | `/admin/consultations/<int:consultation_id>/schedule-request` | `admin.admin_schedule_requested_consultation` |
| POST | `/admin/consultations/<int:consultation_id>/start` | `admin.admin_start_consultation` |
| GET | `/admin/control` | `admin.advisor_control_panel` |
| GET | `/admin/dashboard` | `admin.admin_dashboard` |
| GET | `/admin/dashboard` | `advisor.admin_dashboard` |
| POST | `/admin/drivers/remove/<int:driver_id>` | `admin.remove_driver` |
| POST | `/admin/evidence/<int:evidence_id>/links/reported-concerns/<int:concern_id>` | `evidence_review.link_vehicle_evidence_to_concern` |
| POST | `/admin/evidence/<int:evidence_id>/review` | `evidence_review.review_vehicle_evidence` |
| GET | `/admin/evidence/<int:evidence_id>/workspace` | `evidence_interaction.advisor_evidence_workspace` |
| GET | `/admin/evidence/vehicles/<int:car_id>/pending` | `evidence_interaction.advisor_pending_vehicle_evidence` |
| GET | `/admin/evidence/vehicles/<int:car_id>/timeline` | `advisor_evidence_timeline.advisor_evidence_timeline` |
| GET | `/admin/faults` | `admin.admin_faults_alias` |
| GET | `/admin/fleet/health` | `admin.admin_fleet_health` |
| GET | `/admin/priority-requests` | `admin.admin_priority_queue` |
| POST | `/admin/priority-requests/<int:request_id>/accept` | `admin.admin_accept_priority_request` |
| POST | `/admin/priority-requests/<int:request_id>/cancel` | `admin.admin_cancel_priority_request` |
| GET, POST | `/admin/priority-requests/<int:request_id>/consultation` | `admin.admin_link_priority_consultation` |
| POST | `/admin/priority-requests/<int:request_id>/defer` | `admin.admin_defer_priority_request` |
| POST | `/admin/priority-requests/<int:request_id>/resolve` | `admin.admin_resolve_priority_request` |
| POST | `/admin/priority-requests/<int:request_id>/review` | `admin.admin_review_priority_request` |
| GET | `/admin/rina/provider-status` | `rina_operations.provider_status` |
| GET | `/admin/search` | `admin.admin_global_search` |
| GET | `/admin/treatment-actions` | `admin.treatment_action_console` |
| POST | `/admin/treatment-actions/<int:action_id>/<operation>` | `admin.transition_treatment_action` |
| POST | `/admin/treatment-actions/<int:action_id>/evidence` | `admin.link_treatment_action_evidence` |
| POST | `/admin/treatment-actions/<int:action_id>/schedule` | `admin.schedule_treatment_action` |
| POST | `/admin/treatment-plans/<int:plan_id>/actions` | `admin.create_treatment_action` |
| GET | `/admin/treatment-plans/<int:plan_id>/actions` | `admin.treatment_plan_actions` |
| POST | `/admin/treatment-plans/<int:plan_id>/complete` | `admin.complete_treatment_plan` |
| POST | `/admin/treatment-plans/<int:plan_id>/defer` | `admin.defer_treatment_plan` |
| POST | `/admin/treatment-plans/<int:plan_id>/outcomes` | `admin.record_treatment_outcome` |
| POST | `/admin/treatment-plans/<int:plan_id>/schedule` | `admin.schedule_treatment_plan_for_actions` |
| POST | `/admin/treatment-plans/<int:plan_id>/start` | `admin.start_treatment_plan` |
| POST | `/admin/vehicles/<int:car_id>/decode-vin` | `admin.decode_vehicle_vin` |
| POST | `/admin/vehicles/<int:car_id>/dtcs/<int:dtc_id>/clear` | `admin.clear_vehicle_dtc` |
| POST | `/admin/vehicles/<int:car_id>/dtcs/add` | `admin.add_vehicle_dtc` |
| GET | `/assessments/<int:assessment_id>/report` | `assessment_reports.assessment_report` |
| GET | `/assessments/<int:assessment_id>/report.pdf` | `assessment_reports.assessment_report_pdf` |
| GET | `/audit/advisor/records/<int:event_id>/history` | `clinical_records.advisor_view_record_history` |
| GET | `/audit/records/<int:event_id>/history` | `clinical_records.view_record_history` |
| GET, POST | `/auth/activate/<token>` | `owner_onboarding.activate_account` |
| GET, POST | `/auth/change-password` | `auth.change_password` |
| GET, POST | `/auth/forgot-password` | `auth.forgot_password` |
| GET, POST | `/auth/login` | `auth.login` |
| POST | `/auth/logout` | `auth.logout` |
| POST | `/auth/resend-verification` | `email_verification.resend_verification` |
| GET, POST | `/auth/reset-password/<token>` | `auth.reset_password` |
| GET | `/auth/sessions` | `session_registry.list_sessions` |
| POST | `/auth/sessions/<int:session_id>/revoke` | `session_registry.revoke_session` |
| POST | `/auth/sessions/revoke-others` | `session_registry.revoke_other_sessions` |
| GET, POST | `/auth/signup` | `auth.signup` |
| GET | `/auth/verification-required` | `email_verification.verification_required` |
| GET | `/auth/verify-email` | `email_verification.verify_email` |
| GET | `/cars/` | `cars.get_cars` |
| GET | `/cars/<int:car_id>` | `cars.car_detail` |
| GET | `/cars/<int:car_id>/assessment/report` | `cars.assessment_report` |
| GET, POST | `/cars/<int:car_id>/concerns/add` | `cars.add_reported_concern` |
| GET, POST | `/cars/<int:car_id>/consultations/book` | `cars.book_consultation` |
| POST | `/cars/<int:car_id>/emergency-review` | `cars.request_emergency_review` |
| GET | `/cars/<int:car_id>/faults` | `faults.list_faults` |
| GET, POST | `/cars/<int:car_id>/faults/add` | `faults.add_fault` |
| GET | `/cars/<int:car_id>/health` | `cars.car_health` |
| GET | `/cars/<int:car_id>/maintenance` | `cars.maintenance_intelligence` |
| GET, POST | `/cars/<int:car_id>/priority-request` | `cars.request_priority_scheduling` |
| POST | `/cars/<int:car_id>/priority-requests/<int:request_id>/cancel` | `cars.client_cancel_priority_request` |
| GET | `/cars/<int:car_id>/priority-status` | `cars.client_priority_status` |
| GET | `/cars/<int:car_id>/records` | `cars.vehicle_records` |
| GET | `/cars/<int:car_id>/records/pdf` | `cars.vehicle_records_pdf` |
| GET | `/cars/<int:car_id>/report` | `cars.vehicle_report` |
| GET, POST | `/cars/<int:ownership_id>/service/add` | `cars.add_service_record` |
| GET, POST | `/cars/add` | `cars.add_car` |
| GET | `/cars/debug/run-reminders` | `cars.run_reminders` |
| GET | `/cars/my-vehicles` | `cars.my_vehicles` |
| GET | `/cars/treatment-plans` | `cars.owner_treatment_plans` |
| POST | `/cars/treatment-plans/<int:plan_id>/authorize` | `cars.authorize_treatment_plan` |
| POST | `/chat` | `chat.chat` |
| POST | `/chat/account` | `chat.chat_account` |
| GET | `/chat/context` | `chat.chat_context` |
| GET | `/chat/history` | `chat.chat_history` |
| POST | `/chat/select-vehicle` | `chat.select_chat_vehicle` |
| GET | `/chat/workspace` | `chat.rina_workspace` |
| GET | `/clinical_notices/advisor/health/notices` | `clinical_notices.advisor_all_notices` |
| GET | `/clinical_notices/cars/<int:car_id>/health/notices` | `clinical_notices.client_vehicle_notices` |
| GET | `/dashboard/` | `dashboard.aura_home` |
| POST | `/dashboard/select-vehicle` | `dashboard.select_vehicle` |
| GET | `/driver/cars/<int:car_id>` | `driver.driver_car_view` |
| GET, POST | `/driver/cars/<int:car_id>/check-in` | `driver.driver_daily_checkin` |
| POST | `/driver/cars/<int:car_id>/report` | `driver.driver_report_issue` |
| GET | `/driver/dashboard` | `driver.driver_dashboard` |
| POST | `/evidence/<int:evidence_id>/content` | `evidence.retrieve_evidence_content` |
| POST | `/evidence/<int:evidence_id>/delete` | `evidence.delete_vehicle_evidence` |
| POST | `/evidence/<int:evidence_id>/grant` | `evidence.create_private_retrieval_grant` |
| POST | `/evidence/vehicles/<int:car_id>/images` | `evidence.upload_vehicle_image` |
| GET | `/evidence/vehicles/<int:car_id>/submit` | `evidence_interaction.submit_vehicle_evidence` |
| GET | `/evidence/vehicles/<int:car_id>/timeline` | `evidence_timeline.client_evidence_timeline` |
| GET | `/health/advisor/cars/<int:car_id>/health/records` | `health_records.advisor_vehicle_health_records` |
| GET | `/health/cars/<int:car_id>/health/records` | `health_records.client_vehicle_health_records` |
| GET | `/health_trajectory/cars/<int:car_id>/health/trajectory` | `health_trajectory.vehicle_health_trajectory` |
| GET | `/healthz` | `healthz` |
| GET | `/intelligence/cars/<int:car_id>/health` | `intelligence.get_car_health` |
| GET | `/intelligence/cars/<int:car_id>/rina/insight` | `intelligence.get_rina_insight` |
| GET, POST | `/login` | `login_alias` |
| GET, POST | `/mileage/cars/<int:car_id>/report` | `mileage.report_odometer` |
| GET | `/profile/` | `profiles.profile` |
| GET, POST | `/profile/edit` | `profiles.edit_profile` |
| GET | `/profile/privacy` | `profiles.privacy` |
| POST | `/stewardship/advisor/cars/<int:car_id>/stewardship/reassign` | `stewardship.advisor_reassign_stewardship` |
| GET | `/stewardship/cars/<int:car_id>/stewardship` | `stewardship.get_current_stewardship` |
| GET | `/stewardship/cars/<int:car_id>/stewardship/history` | `stewardship.get_stewardship_history` |
| POST | `/stewardship/cars/<int:car_id>/stewardship/transfer` | `stewardship.request_stewardship_transfer` |
| GET | `/treatments/cars/<int:car_id>/records` | `treatments.get_treatment_records` |
| POST | `/treatments/cars/<int:car_id>/records` | `treatments.record_treatment` |
| DELETE | `/treatments/records/<int:record_id>` | `treatments.archive_treatment_record` |
| PATCH | `/treatments/records/<int:record_id>` | `treatments.update_treatment_record` |
| GET | `/version` | `version` |
