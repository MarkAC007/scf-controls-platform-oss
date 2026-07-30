"""Evaluation harness for CDM control-to-document mapping metrics.

This package holds the DB-free core used to measure top-1 document accuracy
and abstention quality. Runner code may read from the database, but the shared
expectations and variant interfaces remain pure Python so tests can pin the
contract without application or database dependencies.
"""
