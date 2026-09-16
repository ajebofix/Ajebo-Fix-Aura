"""Read-only audit of the existing synthetic GLE maintenance signal."""
import json, os
from sqlalchemy import create_engine, text
uri=os.environ["SQLALCHEMY_DATABASE_URI"]
if uri.startswith("postgres://"):
    uri="postgresql://"+uri[len("postgres://"):]
engine=create_engine(uri)
with engine.connect() as connection:
    with connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '10000ms'"))
        statements={
            "signals": "SELECT id,car_id,ownership_id,alert_type,status,is_active,created_at,resolved_at FROM vehicle_health_alerts WHERE car_id=:car_id AND alert_type='maintenance_monitoring' ORDER BY id",
            "events": "SELECT e.id,e.subject_id,e.event_type,e.actor_type,e.actor_user_id,e.actor_authority,e.previous_state,e.new_state,e.occurred_at,e.recorded_at,e.data->>'source_classification' AS source_classification FROM vehicle_events e JOIN vehicle_health_alerts a ON e.subject_type='vehicle_health_alert' AND e.subject_id=a.id AND e.car_id=a.car_id AND e.ownership_id=a.ownership_id WHERE a.car_id=:car_id AND a.alert_type='maintenance_monitoring' AND e.is_deleted=false ORDER BY e.occurred_at,e.id",
            "service": "SELECT id,event_type,mileage,occurred_at,data->>'maintenance_item_key' AS maintenance_item_key,data->>'record_mode' AS record_mode FROM vehicle_events WHERE car_id=:car_id AND id=55 AND is_deleted=false",
            "odometer": "SELECT id,current_mileage FROM cars WHERE id=:car_id"
        }
        output={name:[dict(row) for row in connection.execute(text(sql),{"car_id":1}).mappings()] for name,sql in statements.items()}
        output["transaction_read_only"]=connection.execute(text("SHOW transaction_read_only")).scalar_one()
        print("MAINTENANCE_RESOLUTION_AUDIT "+json.dumps(output,default=str),flush=True)
