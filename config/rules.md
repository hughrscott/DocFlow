# DocFlow Filing Rules


These rules tell DocFlow where to file each document. The AI reads these rules and uses them as guidance when classifying scanned documents.


Each rule describes a type of document and where it should be filed. When you correct a filing, DocFlow will add or update rules here automatically.


## Pnc Sulis Solar Checking
- **Institution**: pnc
- **Document types**: statement, checking
- **Account hints**: 4102, solar, 7364
- **File to**: Household/PNC
- **Filename**: PNCBankBluebirdSolarChecking{period}.pdf

## Pnc Personal Loc
- **Institution**: pnc
- **Document types**: line_of_credit, loc
- **File to**: PNC/Personal/LOC
- **Filename**: PNCPersonalLineOfCredit{period}.pdf

## Frost Sor Houston Sw
- **Institution**: frost
- **Entity hints**: sor houston, 742 e 20th, school of rock
- **File to**: Frost/SORHoustonSW/Checking
- **Filename**: FrostSORHoustonSWChecking{period}.pdf

## Guardian Insurance Eob
- **Institution**: guardian
- **Document types**: eob, explanation_of_benefits
- **File to**: Insurance/Guardian Health
- **Filename**: GuardianLifeInsuranceEOB{period}{person}.pdf

## Houston Alarm
- **Institution**: city of houston, houston emergency, burglar alarm
- **File to**: Businesses/BluebirdSolar
- **Filename**: HoustonEmergencyAlarmFeeSchedule{period}.pdf

## Bettencourt Tax
- **Institution**: bettencourt
- **Document types**: invoice, statement, bill, tax_notice
- **File to**: Tax/{year} Taxes
- **Filename**: BettencourtTaxAdvisors{doc_type}{period}.pdf

## Lloyds Uk
- **Institution**: lloyds
- **File to**: UK Finance/Lloyds
- **Filename**: LloydsBankAccountStatement{period}.pdf

## Medical Pesikoff
- **Institution**: pesikoff, core primary care, privia
- **Document types**: bill, statement, visit_summary
- **File to**: Medical/Pesikoff
- **Filename**: PesikoffCoreVisitBill{period}{person}.pdf

## Northstar Holdings Registered Agent
- **Institution**: corporate filings, maryland
- **Document types**: invoice
- **File to**: Investments/NorthstarHoldings
- **Filename**: TogetherSolarRegisteredAgent{period}.pdf

## Transnational Sor
- **Institution**: transnational, celero
- **File to**: Businesses/BluebirdSolar
- **Filename**: TransnationalCelero{period}.pdf

## Bluecross Eob
- **Institution**: blue cross, blue shield, bcbs
- **Document types**: eob, explanation_of_benefits
- **File to**: Insurance/BlueCross
- **Filename**: BlueCrossEOB{period}{person}.pdf

## Guardian General
- **Institution**: guardian
- **File to**: Insurance/Guardian Health
- **Filename**: GuardianLifeInsurance{doc_type}{period}.pdf

## Harris County Tax
- **Institution**: harris_county_tax
- **File to**: Tax/{year} Taxes/Harris County
- **Filename**: HarrisCountyPropertyTax{period}.pdf

## Cirro Energy Sor
- **Institution**: cirro_energy
- **Entity hints**: flag store, flagstore
- **File to**: Businesses/BluebirdSolar/Utilities
- **Filename**: CirroEnergy{period}.pdf

## Cirro Energy Personal
- **Institution**: cirro_energy
- **File to**: Utilities/CirroEnergy
- **Filename**: CirroEnergy{period}.pdf

## Usbank Mortgage
- **Institution**: usbank
- **Document types**: statement, bill, invoice
- **File to**: USBank/Mortgage
- **Filename**: USBankMortgage{doc_type}{period}.pdf

## Usbank General
- **Institution**: usbank
- **File to**: USBank
- **Filename**: USBank{doc_type}{period}.pdf

## Routt County Tax
- **Institution**: routt_county
- **File to**: Tax/{year} Taxes/Routt County
- **Filename**: RouttCountyPropertyTax{period}.pdf

## Rippling W2
- **Institution**: rippling
- **Document types**: w2
- **File to**: Tax/{year} Taxes/W2
- **Filename**: RipplingW2{period}.pdf

## Rippling General
- **Institution**: rippling
- **File to**: Tax/{year} Taxes/Payroll
- **Filename**: Rippling{doc_type}{period}.pdf

## Tx Workforce
- **Institution**: tx_workforce
- **File to**: Business/Texas Workforce Commission
- **Filename**: TexasWorkforceCommission{doc_type}{period}.pdf

## Tx Comptroller Gs
- **Institution**: tx_comptroller
- **Entity hints**: gs consulting, greatscott, great scott
- **File to**: Giving/SampletonFoundation/Texas Comptroller
- **Filename**: TexasComptroller{doc_type}{period}.pdf

## Tx Comptroller General
- **Institution**: tx_comptroller
- **File to**: Tax/{year} Taxes/Texas Comptroller
- **Filename**: TexasComptroller{doc_type}{period}.pdf
