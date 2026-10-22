
    timestamp = datetime.now(timezone.utc).isoformat()
    data["server"] = edge_id
    data["cloud_trust"] = round(current_trust, 3)
    data["cloud_direct_trust"] = round(direct_trust, 3)
    data["cloud_interactions"] = edge_trust[edge_id]["count"]
    data["log_time"] = timestamp
    data["cloud_validation"] = reason

    # Reject invalid/corrupted data and low-trust sources.
    if not valid:
        print(
            f"REJECTED {edge_id}: {reason}; "
            f"cloud trust={current_trust:.3f}"
        )
        raise HTTPException(
            status_code=422,
            detail={
                "status": "rejected_invalid_data",
                "edge_id": edge_id,
                "reason": reason,
                "cloud_trust": round(current_trust, 3),
            },
        )

    if current_trust < TRUST_THRESHOLD:
        print(
            f"REJECTED {edge_id}: trust "
            f"{current_trust:.3f} < {TRUST_THRESHOLD}"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "status": "rejected_low_trust",
                "edge_id": edge_id,
                "cloud_trust": round(current_trust, 3),
            },
        )

    # Store accepted reports in memory.
    accepted_record = dict(data)
    data_logs.insert(0, accepted_record)

    if len(data_logs) > MAX_LOGS:
        data_logs.pop()

    # Keep the highest-trust accepted record.
    if (
        best_edge_record is None
        or current_trust
        > float(best_edge_record.get("cloud_trust", 0.0))
    ):
        best_edge_record = dict(accepted_record)

    print(
        f"[CLOUD ACCEPTED] {timestamp} | {edge_id} | "
        f"T={current_trust:.3f} | direct={direct_trust:.3f} | "
        f"pitch={data.get('smoothed_pitch', data.get('pitch'))} | "
        f"roll={data.get('smoothed_roll', data.get('roll'))}"
    )

    return {
        "status": "logged",
        "edge_id": edge_id,
        "cloud_trust": round(current_trust, 3),
        "cloud_direct_trust": round(direct_trust, 3),
        "best_edge": (
            best_edge_record.get("server")
            if best_edge_record else None
        ),
    }
