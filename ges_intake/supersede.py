from ges_intake.process import parse_timestamp


def apply_supersession(all_documents, doc_types_lookup):
    """Apply supersession rules across all documents.

    Document X supersedes document Y when:
    - X's type has a supersede block and Y's type is in supersedes list
    - Every field in X's supersede.key is present in X and equal in Y
    - (X.received_at, X.document_id) > (Y.received_at, Y.document_id)
    - Neither is quarantined
    """
    for i, doc_x in enumerate(all_documents):
        if doc_x["status"] == "quarantined":
            continue
        matched_type = doc_x.get("_matched_type")
        if not matched_type:
            continue
        supersede_config = matched_type.get("supersede")
        if not supersede_config:
            continue

        key_fields = supersede_config.get("key", [])
        supersedes_types = set(supersede_config.get("supersedes", []))
        x_fields = doc_x.get("_fields", {})

        # Check all key fields are present in X
        x_keys = {}
        all_keys_present = True
        for kf in key_fields:
            val = x_fields.get(kf)
            if val is None:
                all_keys_present = False
                break
            x_keys[kf] = val
        if not all_keys_present:
            continue

        x_received = parse_timestamp(doc_x.get("_entry", {}).get("received_at", ""))
        x_id = doc_x["document_id"]

        for j, doc_y in enumerate(all_documents):
            if i == j:
                continue
            if doc_y["status"] == "quarantined":
                continue
            if doc_y["status"] == "superseded":
                continue
            if doc_y.get("doc_type") not in supersedes_types:
                continue

            y_fields = doc_y.get("_fields", {})

            # Check all key fields match
            keys_match = True
            for kf in key_fields:
                if y_fields.get(kf) != x_keys[kf]:
                    keys_match = False
                    break
            if not keys_match:
                continue

            y_received = parse_timestamp(doc_y.get("_entry", {}).get("received_at", ""))
            y_id = doc_y["document_id"]

            # X supersedes Y if (X.received_at, X.document_id) > (Y.received_at, Y.document_id)
            if x_received and y_received:
                if (x_received, x_id) > (y_received, y_id):
                    doc_y["status"] = "superseded"
                elif (y_received, y_id) > (x_received, x_id):
                    doc_x["status"] = "superseded"
                    break  # X is superseded, stop checking
