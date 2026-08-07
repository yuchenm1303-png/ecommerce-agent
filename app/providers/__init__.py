"""Optional semantic extraction provider adapters.

Core resolver modules never depend on a specific model vendor. Provider modules
translate the strict grounded request into one vendor API and return untrusted
JSON that must still pass app.semantic_extraction validation.
"""
