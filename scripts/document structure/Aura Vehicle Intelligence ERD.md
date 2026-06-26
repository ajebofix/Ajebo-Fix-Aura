<!-- scripts/document structure/Aura Vehicle Intelligence ERD.md -->

This ERD is designed to fit Aura's existing clinical architecture rather than becoming a generic database.

# Aura Vehicle Intelligence ERD (V1)


+------------------+
                               |       User       |
                               +------------------+
                                         |
                                         |
                               CarOwnership (1:N)
                                         |
                                         |
                                 +---------------+
                                 |      Car      |
                                 +---------------+
                                 | id            |
                                 | vin           |
                                 | brand         |
                                 | model         |
                                 | year          |
                                 | mileage       |
                                 | engine_type   |
                                 | transmission  |
                                 +---------------+
                                   |     |      |      |
                    1:1            |     |      |      | 1:N
                                   |     |      |      |
                                   |     |      |      +-----------------------------+
                                   |     |      |                                    |
                                   |     |      |                                    |
                         +--------------------+  |                         +----------------------+
                         | VehicleProfile     |  |                         | MaintenanceSchedule  |
                         +--------------------+  |                         +----------------------+
                         | id                 |  |                         | id                   |
                         | car_id (unique)    |  |                         | car_id               |
                         | trim               |  |                         | service_name         |
                         | body_style         |  |                         | due_mileage          |
                         | fuel_type          |  |                         | due_date             |
                         | drive_type         |  |                         | completed_at         |
                         | plant_country      |  |                         | status               |
                         | vin_decoded        |  |                         | source               |
                         | decoded_at         |  |                         +----------------------+
                         | source             |  |
                         +--------------------+  |
                                                 |
                                                 |
                                      +----------------------+
                                      |     VehicleDTC       |
                                      +----------------------+
                                      | id                   |
                                      | car_id               |
                                      | code                 |
                                      | code_type            |
                                      | description          |
                                      | severity             |
                                      | status               |
                                      | detected_at          |
                                      | source               |
                                      +----------------------+
                                                 |
                                                 |
                                      +----------------------+
                                      |    VehicleRecall     |
                                      +----------------------+
                                      | id                   |
                                      | car_id               |
                                      | recall_number        |
                                      | title                |
                                      | summary              |
                                      | risk_level           |
                                      | is_open              |
                                      | published_at         |
                                      | source               |
                                      +----------------------+




# Entity Responsibilities

## 1. Car (Source of Truth)

The existing Car model remains the canonical vehicle identity.

It owns:

* VIN
* Brand
* Model
* Year
* Mileage
* Engine type
* Transmission
* Plate number
* Ownership relationships

This prevents duplicated data.

⸻

## 2. VehicleProfile (1:1)

Purpose: Store decoded intelligence, not identity.

    Field                                       Purpose

trim                                        Vehicle trim level

body_style                                  Sedan, SUV, Coupe

fuel_type                                   Petrol, Diesel, Hybrid, EV

drive_type                                  FWD, RWD, AWD, 4MATIC

plant_country                               Manufacturing origin

vin_decoded                                 Whether VIN has been successfully decoded

decoded_at                                  Last decode timestamp

source                                      VIN decoding provider



## 3. VehicleDTC (1:N)

Stores active and historical diagnostic trouble codes.

Additional recommended fields:


    Field                                                   Purpose

code                                                    P0300, U0100, etc.

code_type                                               SAE or OEM

affected_system                                         Powertrain, Chassis, Network, Body

severity                                                Information, Attention, Elevated, Critical

status                                                  Active, Cleared, Historical

advisor_note                                            Optional internal summary

detected_at                                             Detection timestamp

cleared_at                                              Optional clear timestamp

source                                                  Scanner/API/manual


This stores vehicle intelligence, not repair recommendations.

⸻

## 4. VehicleRecall (1:N)

Tracks manufacturer safety recalls.

Additional recommended fields:


    Field                                   Purpose

recall_number                           Official recall ID

title                                   Recall title

summary                                 Brief description

risk_level                              Low, Medium, High, Critical

is_open                                 Open vs completed

published_at                            Recall publication date

closed_at                               Optional completion date

source                                  NHTSA/OEM


⸻

## 5. MaintenanceSchedule (1:N)

Represents structured maintenance planning.

Additional recommended fields:


    Field                                                   Purpose

service_name                                        Service A, Oil Change, Brake Fluid

due_mileage                                         Scheduled mileage

due_date                                            Scheduled date

completed_at                                        Completion timestamp

status                                              Upcoming, Due, Overdue, Completed

source                                              NHTSA/OEM, Advisor, Manual


⸻

# Relationships

    Relationships                       Cardinality

Car → VehicleProfile                        1 : 1

Car → VehicleDTC                            1 : N

Car → VehicleRecall                         1 : N

Car → MaintenanceSchedule                   1 : N


No relationships are required between these intelligence tables themselves. Everything is anchored to the Car, keeping queries simple and avoiding unnecessary coupling.

⸻

# ERD Design Principles

This ERD follows the same architectural rules you’ve established for Aura:

* Single Source of Truth: Vehicle identity stays in Car.
* Extension over duplication: VehicleProfile enriches rather than copies vehicle data.
* Clinical intelligence: Tables describe the vehicle’s condition and context, not repair procedures.
* Advisor-first: Data supports summaries, prioritization, and continuity.
* Future-ready: The design leaves room for OEM connectors, VIN decoding services, recall APIs, and DTC libraries without changing the schema.

# ✅ ERD Status

I would consider the Vehicle Intelligence ERD finalized.

The implementation order should now be:

1. VehicleProfile (1:1 extension of Car)
2. VehicleDTC
3. VehicleRecall
4. MaintenanceSchedule

Once those models are in place, the intelligence services (vin_decoder.py, dtc_decoder.py, maintenance_engine.py, and recall_service.py) can be built cleanly on top of this database foundation.