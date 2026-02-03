"""
Unit tests for the Utils module.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from bridgic.browser.utils import find_page_by_id, generate_page_id
from bridgic.browser.utils._schema_helper import get_field_descriptions


class TestPageIdFunctions:
    """Tests for page ID generation and lookup functions."""

    def test_generate_page_id(self):
        """Test generating page ID from a page object."""
        mock_page = MagicMock()

        page_id = generate_page_id(mock_page)

        assert page_id is not None
        assert isinstance(page_id, str)
        assert len(page_id) > 0

    def test_generate_page_id_consistent(self):
        """Test that page ID is consistent for same page."""
        mock_page = MagicMock()

        page_id1 = generate_page_id(mock_page)
        page_id2 = generate_page_id(mock_page)

        assert page_id1 == page_id2

    def test_generate_page_id_unique(self):
        """Test that different pages get different IDs."""
        mock_page1 = MagicMock()
        mock_page2 = MagicMock()

        page_id1 = generate_page_id(mock_page1)
        page_id2 = generate_page_id(mock_page2)

        assert page_id1 != page_id2

    def test_find_page_by_id_found(self):
        """Test finding a page by ID when it exists."""
        mock_page1 = MagicMock()
        mock_page2 = MagicMock()
        pages = [mock_page1, mock_page2]

        target_id = generate_page_id(mock_page1)
        found_page = find_page_by_id(pages, target_id)

        assert found_page is mock_page1

    def test_find_page_by_id_not_found(self):
        """Test finding a page by ID when it doesn't exist."""
        mock_page = MagicMock()
        pages = [mock_page]

        found_page = find_page_by_id(pages, "nonexistent_id")

        assert found_page is None

    def test_find_page_by_id_empty_list(self):
        """Test finding a page in empty list."""
        found_page = find_page_by_id([], "some_id")

        assert found_page is None


class TestSchemaHelper:
    """Tests for Pydantic schema helper functions."""

    def test_get_field_descriptions_basic(self):
        """Test getting field descriptions from a Pydantic model."""

        class TestModel(BaseModel):
            name: str = Field(description="The name field")
            age: int = Field(description="The age field")

        result = get_field_descriptions(TestModel)

        # Function returns a markdown string
        assert isinstance(result, str)
        assert "name" in result
        assert "The name field" in result
        assert "age" in result
        assert "The age field" in result

    def test_get_field_descriptions_no_description(self):
        """Test getting field descriptions when some fields have no description."""

        class TestModel(BaseModel):
            name: str = Field(description="The name field")
            age: int  # No description

        result = get_field_descriptions(TestModel)

        assert "name" in result
        assert "The name field" in result
        # age may or may not appear depending on implementation

    def test_get_field_descriptions_nested_model(self):
        """Test getting field descriptions from nested model."""

        class Address(BaseModel):
            street: str = Field(description="Street address")
            city: str = Field(description="City name")

        class Person(BaseModel):
            name: str = Field(description="Person name")
            address: Address = Field(description="Person address")

        result = get_field_descriptions(Person)

        assert "name" in result
        assert "address" in result

    def test_get_field_descriptions_with_defaults(self):
        """Test getting field descriptions with default values."""

        class TestModel(BaseModel):
            name: str = Field(default="Unknown", description="The name")
            active: bool = Field(default=True, description="Is active")

        result = get_field_descriptions(TestModel)

        assert "The name" in result
        assert "Is active" in result

    def test_get_field_descriptions_optional_fields(self):
        """Test getting field descriptions for optional fields."""
        from typing import Optional

        class TestModel(BaseModel):
            required_field: str = Field(description="Required field")
            optional_field: Optional[str] = Field(
                default=None, description="Optional field"
            )

        result = get_field_descriptions(TestModel)

        assert "Required field" in result
        assert "Optional field" in result


class TestLoggingConfiguration:
    """Tests for logging configuration."""

    def test_configure_logging_import(self):
        """Test that configure_logging can be imported."""
        from bridgic.browser.utils._logging import configure_logging

        assert callable(configure_logging)

    def test_configure_logging_basic(self):
        """Test basic logging configuration."""
        from bridgic.browser.utils._logging import configure_logging

        # This should not raise - level is a string
        configure_logging(level="DEBUG")

    def test_configure_logging_with_format(self):
        """Test logging configuration with custom format."""
        from bridgic.browser.utils._logging import configure_logging

        # This should not raise
        configure_logging(level="INFO", format_string="%(levelname)s: %(message)s")

    def test_configure_logging_invalid_level(self):
        """Test logging configuration with invalid level."""
        from bridgic.browser.utils._logging import configure_logging

        with pytest.raises(ValueError):
            configure_logging(level="INVALID_LEVEL")
