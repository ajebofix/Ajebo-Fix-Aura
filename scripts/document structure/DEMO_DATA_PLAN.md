<!-- document structure/DEMO_DATA_PLAN.md -->

This document defines all demo records before you start inserting anything.


# Demo Fleet

Create 12 vehicles.

## Healthy Vehicles (4)


    Client                                          Vehicle

Adekunle Williams                       2022 Mercedes-Benz C300

Chinedu Okafor                          2021 Mercedes-Benz GLC300

Aisha Bello                             2023 Mercedes-Benz E300

David Thomas                            2020 Lexus RX350


## Monitoring Vehicles (4)


    Client                                          Vehicle

Tunde Adebayo                               2021 Mercedes-Benz GLE450

Femi Johnson                                2019 Mercedes-Benz S560

Grace Nwosu                                 2018 Toyota Prado

Emeka Obi                                   2020 BMW X5


## Attention / Priority Vehicles (4)

    Client                                          Vehicle

Mrs. Agbo                                   2019 Mercedes-Benz E350

Victor Adewale                              2017 Mercedes-Benz GLS450

Ibrahim Musa                                2018 Range Rover Sport

Kunle Akin                                  2016 Mercedes-Benz ML350




# Health Status Distribution

Make Aura look realistic.

## Healthy

4 vehicles

## Monitoring

4 vehicles

## Attention

3 vehicles

## Critical

1 vehicle

This creates meaningful dashboard statistics.

⸻

# Concerns

Target:

## 40 concerns

Distribution:

    Category                                Count

Steering Wheel                              6

Cooling System                              8

Suspension                                  5

Brakes                                      5

Electrical                                  7

Engine                                      4

Warning Lights                              5


# Consultations

Create:

## 18 consultations

Status split:


    Status                          Count

Requested                           4

Approved                            4

In Progress                         6

Completed                           4


This makes the Consultation Queue look alive.

⸻

# Treatment Plans

Create:

## 12 treatment plans

Status split:



    Status                  Count

Approved                    3

Scheduled                   3

In Progress                 3

Monitoring                  2

Completed                   1


Exactly aligned with Aura’s care progression model.

⸻

# Advisor Notes

Create:

## 25 advisor notes

Examples:

### Tunde Adebayo

Frequently travels between Lagos and Abuja.

⸻

### Mrs. Agbo

Reports symptoms early and follows recommendations quickly.

⸻

### Victor Adewale

Previously deferred cooling-system review twice.

⸻

### Ibrahim Musa

Prefers WhatsApp communication.

These make the Advisor CRM feel real.

⸻

# Conversation Records

Target:

## 60 records

Examples:

## Record 1

Concern:
Steering vibration

Urgency:
Moderate

Escalation:
Review Advised

⸻

## Record 2

Concern:
Coolant loss

Urgency:
High

Escalation:
Priority Review

⸻

## Record 3

Concern:
Warning light returned

Urgency:
Moderate

Escalation:
Monitoring

These should eventually feed the clinical conversation layer.

⸻

# Priority Queue

Create realistic queue entries.

## Critical

Mrs. Agbo

Repeated coolant loss.

⸻

## Priority

Victor Adewale

Recurring suspension concern.

⸻

## Monitoring

Tunde Adebayo

Steering instability progression.

This makes the Admin Console immediately understandable.

⸻

# Sprint A Goal

By the end of this step:

* Dashboard has data
* Client Registry has data
* Consultations have data
* Treatment Plans have data
* Advisor Notes have data
* Priority Queue has data

Nobody should ever see an empty screen.

After DEMO_DATA_PLAN.md is written, the next task is:

### Generate the actual SQLAlchemy seed script (seed_demo_data.py) that populates all of this automatically.