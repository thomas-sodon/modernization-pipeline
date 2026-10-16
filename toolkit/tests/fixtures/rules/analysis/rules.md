# Business rules: booklend-demo

Spec version 1.1. Synthetic fixture for test_spec_check.py; every rule is invented.

## catalog

- **R-catalog-001**: each book carries a catalogue code that no other book uses.

## lending

- **R-lending-001**: a member whose unpaid fines exceed the limit cannot borrow another book.
- **R-lending-002**: a loan is overdue when its due date has passed and the book is not back.
- **R-lending-003**: checking a book out and printing the overdue notice follow one path.

## Open questions

- **Q-lending-001**: is the fine limit per member or per household?

## Sign-off

Signed for the client: ________________  Date: __________
