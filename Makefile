# Makefile for some convenient operations.

PY_FILES = $(shell find alec -name '*.py' -type f | sort)
UNITTESTS = $(shell find alec -name '*_unittest.py' | sort)

LINT_OPTIONS = --rcfile=bin/pylintrc \
	       --msg-template='{path}:{line}: {msg_id}: {msg}' \
	       --generated-members='service_pb2.*'

all: test lint

test:
	@for test in $(UNITTESTS); do \
	   echo Running $$test ...; \
	   $$test || exit 1; \
	 done

lint:
	@pep8 $(PY_FILES)
	@pylint $(LINT_OPTIONS) $(PY_FILES)

format:
	@yapf -d $(PY_FILES)
