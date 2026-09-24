from __future__ import annotations

from app.makro.portal_adapter import ListingStage, MakroPortalAdapter


class CollapsedStep3Page:
    url = "https://seller.makro.co.za/#dashboard/addListings/single-transition"

    def evaluate(self, _script):
        return {
            "text": (
                "Add a Single Listing\n"
                "3 ADD PRODUCT INFO\n"
                "Please fill all mandatory attributes to preview title\n"
                "Product Photos (0/5)\n"
                "Price, Stock and Shipping Information (0/14)\n"
                "Product Description (0/10)\n"
                "Additional Description (Optional) (0/12)\n"
                "EDIT\nEDIT\nEDIT"
            ),
            "editActions": 3,
        }


class NonMakroPage(CollapsedStep3Page):
    url = "https://example.com/product"


def test_collapsed_step3_structure_wins_before_route_parse_or_input_density():
    adapter = MakroPortalAdapter(CollapsedStep3Page())

    assert adapter.detect_stage() is ListingStage.PRODUCT_INFO


def test_step3_structure_signal_is_bound_to_makro_host():
    adapter = MakroPortalAdapter(NonMakroPage())

    assert adapter.detect_stage() is ListingStage.UNKNOWN
