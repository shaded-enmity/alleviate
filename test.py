import alleviate

mode = 'r'
def main():
    try:
        open('text.py', mode)
    except Exception as e:
        alleviate.exception(e)#, output=alleviate.Output.JSON)

main()
