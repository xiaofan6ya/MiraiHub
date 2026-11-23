import{a9 as N,j as H,p as v,m as j,w as A,x as w,A as E}from"./index-DmuWfX0N.js";import{H as O,d as b}from"./vendor-naive-SrciwSnN.js";import{h as V,k,m as F,F as L,c as y}from"./vendor-vue-01Qz8dz3.js";let S=!1;function T(){if(N&&window.CSS&&!S&&(S=!0,"registerProperty"in window?.CSS))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function $(e){const{heightSmall:i,heightMedium:o,heightLarge:a,borderRadius:s}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:s,heightSmall:i,heightMedium:o,heightLarge:a}}const I={common:H,self:$},K=v([j("skeleton",`
 height: 1em;
 width: 100%;
 transition:
 --n-color-start .3s var(--n-bezier),
 --n-color-end .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 animation: 2s skeleton-loading infinite cubic-bezier(0.36, 0, 0.64, 1);
 background-color: var(--n-color-start);
 `),v("@keyframes skeleton-loading",`
 0% {
 background: var(--n-color-start);
 }
 40% {
 background: var(--n-color-end);
 }
 80% {
 background: var(--n-color-start);
 }
 100% {
 background: var(--n-color-start);
 }
 `)]),M=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),G=V({name:"Skeleton",inheritAttrs:!1,props:M,setup(e){T();const{mergedClsPrefixRef:i,mergedComponentPropsRef:o}=A(e),a=y(()=>{var r,n;return e.size||((n=(r=o?.value)===null||r===void 0?void 0:r.Skeleton)===null||n===void 0?void 0:n.size)}),s=w("Skeleton","-skeleton",K,I,e,i);return{mergedClsPrefix:i,style:y(()=>{var r,n;const g=s.value,{common:{cubicBezierEaseInOut:z}}=g,h=g.self,{color:x,colorEnd:C,borderRadius:P}=h;let l;const{circle:d,sharp:B,round:R,width:t,height:c,text:f,animated:_}=e,p=a.value;p!==void 0&&(l=h[E("height",p)]);const u=d?(r=t??c)!==null&&r!==void 0?r:l:t,m=(n=d?t??c:c)!==null&&n!==void 0?n:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":R?"4096px":B?"":P,width:typeof u=="number"?b(u):u,height:typeof m=="number"?b(m):m,animation:_?"":"none","--n-bezier":z,"--n-color-start":x,"--n-color-end":C}})}},render(){const{repeat:e,style:i,mergedClsPrefix:o,$attrs:a}=this,s=k("div",F({class:`${o}-skeleton`,style:i},a));return e>1?k(L,null,O(e,null).map(r=>[s,`
`])):s}});export{G as N};
