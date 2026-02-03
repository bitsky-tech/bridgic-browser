from pydantic import BaseModel


def get_field_descriptions(model_class):
    """Extract field descriptions from a Pydantic model.

    Parameters
    ----------
    model_class
        A Pydantic model class (Pydantic v2) that supports
        `model_json_schema()`.

    Returns
    -------
    str
        A markdown string that includes the model-level schema description
        (if present) and a bullet list of field descriptions.
    """
    schema = model_class.model_json_schema()
    fields_info = []
    schema_desc = schema.get("description", "")
    if "properties" in schema:
        for field_name, field_info in schema["properties"].items():
            desc = field_info.get("description", "")
            field_type = field_info.get("type", "")
            if desc:
                fields_info.append(f"- {field_name}: {desc}")
    return f"### {schema_desc}\n\n" + "\n".join(fields_info)

def model_to_llm_string(obj: BaseModel) -> str:
    """Convert a Pydantic model to a LLM-friendly, minimal string format."""
    data = obj.model_dump(exclude_none=True, by_alias=True)
    parts = []
    for k, v in data.items():
        if isinstance(v, list):
            parts.append(f"{k}=[{','.join(map(str, v))}]")
        else:
            parts.append(f"{k}={v}")
    return "; ".join(parts)
