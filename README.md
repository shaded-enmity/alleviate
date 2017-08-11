# alleviate
Python framework for comfortable exception handling and exception information gathering suited for standard library functions but extendable to cover cover custom use cases as well.

## Examples / What is implemented now

Attempting to open a file which doesn't exist:
```
$ python test.py
Program error
-------------

File /home/podvody/Repos/alleviate/text.py could not be found

Errno:  2 (ENOENT)
Action: open


Symptoms
--------

   File does not exist: /home/podvody/Repos/alleviate/text.py


Solutions
---------

   Check out files with similar name:

      /home/podvody/Repos/alleviate/test.py similarity: 85%
```

The same information can also be formatted as JSON:
```
# python test_json.py
{
   "symptoms": [
      {
         "name": "file_does_not_exist", 
         "value": "/home/podvody/Repos/alleviate/text.py"
      }, 
      {
         "name": "error_code", 
         "value": 2
      }
   ], 
   "exception": "IOError", 
   "description": "File /home/podvody/Repos/alleviate/text.py could not be found\n\nErrno:  2 (ENOENT)\nAction: open", 
   "solutions": [
      "/home/podvody/Repos/alleviate/test.py similarity: 85%"
   ]
}

```
